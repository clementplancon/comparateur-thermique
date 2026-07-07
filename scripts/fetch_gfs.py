#!/usr/bin/env python3
"""
Recolte la temperature a 2 m de toute la planete "maintenant" depuis le modele
GFS de la NOAA (domaine public, sans cle), masque les oceans, et ecrit
data/temps.json.

Source : miroirs open-data AWS S3 / Google (fichiers statiques, fiables, sans
rate-limit, requetes par plage). On lit l'index .idx pour ne telecharger que les
messages GRIB "temperature 2 m" et "masque terre/mer", puis on les decode.

Pourquoi pas Open-Meteo : son offre gratuite facture *un appel par point*
(10 000/jour, 600/min) -> un champ mondial detaille est impossible.
Pourquoi pas le "grib filter" NOMADS : ce CGI renvoie souvent des erreurs 500.
Les fichiers S3/Google, eux, sont des objets statiques tres stables.

Dependances : numpy, xarray, cfgrib, eccodes (toutes via pip, binaires inclus).
"""

import os, sys, json, tempfile, urllib.request
import datetime as dt
import numpy as np
import xarray as xr

# ---- Reglages ----
RES_TAG = "0p50"             # "0p50" (0.5°, conseille) ou "0p25" (0.25°, 4x plus lourd)
STEP_H  = 3                  # pas des echeances : 3 h pour 0p50, 1 h possible pour 0p25
LAT_MIN, LAT_MAX = -60, 84   # poles ignores (aucune ville au-dela)
LAND_THRESHOLD = 0.5          # GFS LAND >= 0.5 : maille majoritairement terrestre
AXIS_ATOL = 1e-6              # tolerance sur les axes lat/lon apres normalisation
OUT_PATH = "data/temps.json"
UA = {"User-Agent": "isotherme/1.0 (open-data temperature grid)"}

# hotes miroir (fichiers identiques) : AWS d'abord, Google en secours
HOSTS = [
    "https://noaa-gfs-bdp-pds.s3.amazonaws.com",
    "https://storage.googleapis.com/global-forecast-system",
]

def key_for(cycle, fhour):
    ymd = cycle.strftime("%Y%m%d"); hh = cycle.strftime("%H")
    return f"gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.{RES_TAG}.f{fhour:03d}"

def http_get(url, rng=None, timeout=120):
    req = urllib.request.Request(url, headers=dict(UA))
    if rng:
        req.add_header("Range", f"bytes={rng}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def field_range(idx_text, marker):
    """Trouve l'octet de debut/fin d'un message GRIB dans l'index .idx."""
    lines = [l for l in idx_text.splitlines() if l.strip()]
    for i, ln in enumerate(lines):
        if marker in ln:
            start = int(ln.split(":")[1])
            end = None
            if i + 1 < len(lines):
                nxt = lines[i + 1].split(":")
                if len(nxt) > 1 and nxt[1].isdigit():
                    end = int(nxt[1])
            return start, end
    return None, None

def fetch_message(cycle, fhour, marker, label):
    """Pour un (cycle, echeance) donne, telecharge un seul message GRIB.
    Renvoie les octets GRIB ou None si indisponible sur tous les miroirs."""
    key = key_for(cycle, fhour)
    for host in HOSTS:
        grib_url = f"{host}/{key}"
        idx_url = grib_url + ".idx"
        try:
            idx = http_get(idx_url, timeout=30).decode("utf-8", "replace")
        except Exception as e:
            print(f"    idx absent ({host.split('//')[1].split('/')[0]}): {e}")
            continue
        s, e = field_range(idx, marker)
        if s is None:
            print(f"    {label} introuvable dans l'index")
            continue
        rng = f"{s}-{e-1}" if e else f"{s}-"
        try:
            data = http_get(grib_url, rng=rng)
        except Exception as ex:
            print(f"    echec range: {ex}")
            continue
        if data[:4] == b"GRIB":
            print(f"  {label} ok via {host.split('//')[1].split('/')[0]} ({len(data)//1024} Ko)")
            return data
        print("    octets non-GRIB")
    return None

def find_source():
    """Cycle GFS le plus recent disponible + echeance la plus proche de maintenant."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for back in range(0, 30, 6):
        t = now - dt.timedelta(hours=5 + back)          # ~5 h de latence de publication
        cyc = t.replace(minute=0, second=0, microsecond=0, hour=(t.hour // 6) * 6)
        lead = (now - cyc).total_seconds() / 3600.0
        fhour = max(0, min(int(round(lead / STEP_H) * STEP_H), 120))
        print(f"  essai {cyc:%Y-%m-%d %H}Z f{fhour:03d} …")
        temp = fetch_message(cyc, fhour, ":TMP:2 m above ground:", "TMP 2 m")
        land = fetch_message(cyc, fhour, ":LAND:surface:", "masque terre/mer")
        if temp and land:
            return cyc, fhour, temp, land
    raise SystemExit("Aucun cycle GFS disponible sur les miroirs open-data.")

def read_field(grib_bytes):
    """Decode un champ GRIB + axes lat/lon."""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as f:
            f.write(grib_bytes); path = f.name
        ds = xr.open_dataset(path, engine="cfgrib", backend_kwargs={"indexpath": ""})
        name = list(ds.data_vars)[0]
        da = ds[name]
        return da["latitude"].values, da["longitude"].values, da.values
    finally:
        for p in ((path, path + ".idx") if path else ()):
            if p and os.path.exists(p):
                try: os.unlink(p)
                except OSError: pass

def normalize_field(lats, lons, values):
    """Normalise (nord en haut, lon -180..180, crop)."""
    arr = values.astype("float64")
    lats = np.asarray(lats, dtype="float64")
    lons = np.asarray(lons, dtype="float64")

    if lats[0] < lats[-1]:
        lats = lats[::-1]; arr = arr[::-1, :]

    lons = ((lons + 180.0) % 360.0) - 180.0
    order = np.argsort(lons)
    lons = lons[order]; arr = arr[:, order]

    mask = (lats <= LAT_MAX + 1e-6) & (lats >= LAT_MIN - 1e-6)
    lats = lats[mask]; arr = arr[mask, :]
    return lats, lons, arr

def encode_rle(mask):
    """Encode un masque booleen en longueurs alternees pour alleger le JSON."""
    flat = mask.astype("uint8").ravel()
    if flat.size == 0:
        return 0, []
    start = int(flat[0])
    runs = []
    current = start
    count = 0
    for v in flat:
        v = int(v)
        if v == current:
            count += 1
        else:
            runs.append(count)
            current = v
            count = 1
    runs.append(count)
    return start, runs

def build(temp_lats, temp_lons, kelvin, land_lats, land_lons, land_values, cycle, fhour):
    """Normalise, masque les oceans, et serialise."""
    lats, lons, temp = normalize_field(temp_lats, temp_lons, kelvin)
    mask_lats, mask_lons, land = normalize_field(land_lats, land_lons, land_values)
    if temp.shape != land.shape:
        raise SystemExit(f"Le masque terre/mer ({land.shape}) ne correspond pas à la grille temperature ({temp.shape}).")
    axes_match = (
        np.allclose(lats, mask_lats, rtol=0, atol=AXIS_ATOL)
        and np.allclose(lons, mask_lons, rtol=0, atol=AXIS_ATOL)
    )
    if not axes_match:
        raise SystemExit(
            "Le masque terre/mer ne correspond pas aux axes temperature "
            f"(lat {mask_lats[0]}..{mask_lats[-1]}, lon {mask_lons[0]}..{mask_lons[-1]} "
            f"vs lat {lats[0]}..{lats[-1]}, lon {lons[0]}..{lons[-1]})."
        )

    land_mask = land >= LAND_THRESHOLD
    arr = np.where(land_mask, temp - 273.15, np.nan)
    res = round(float(abs(lats[0] - lats[1])), 4)
    ny, nx = arr.shape
    temps = [round(float(v), 1) if np.isfinite(v) else None for v in arr.ravel()]
    land_start, land_rle = encode_rle(land_mask)

    valid = (cycle + dt.timedelta(hours=fhour)).replace(microsecond=0)
    return {
        "generated": valid.isoformat() + "Z",
        "source": f"NOAA GFS {res}°",
        "res": res,
        "latMin": round(float(lats[-1]), 4),
        "latMax": round(float(lats[0]), 4),
        "lonMin": round(float(lons[0]), 4),
        "lonMax": round(float(lons[-1] + res), 4),
        "nx": nx, "ny": ny,
        "temps": temps,
        "landStart": land_start,
        "landRle": land_rle,
    }

def main():
    print(f"GFS {RES_TAG} · temperature 2 m mondiale (miroirs open-data)")
    cycle, fhour, temp_grib, land_grib = find_source()
    temp_lats, temp_lons, kelvin = read_field(temp_grib)
    land_lats, land_lons, land_values = read_field(land_grib)
    out = build(temp_lats, temp_lons, kelvin, land_lats, land_lons, land_values, cycle, fhour)

    n_valid = sum(1 for v in out["temps"] if v is not None)
    if n_valid == 0:
        raise SystemExit("Champ vide apres lecture.")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    size = os.path.getsize(OUT_PATH) / 1024
    print(f"OK : {OUT_PATH} ({out['nx']}x{out['ny']} = {len(out['temps'])} points, "
          f"{n_valid} valides, {size:.0f} Ko) — valide {out['generated']}")

if __name__ == "__main__":
    main()
