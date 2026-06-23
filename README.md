# Isotherme

Comparateur thermique mondial. On choisit un lieu (recherche ou clic), puis on revele
en degrade lisse toutes les zones de la planete ou il fait actuellement **plus chaud**
ou **plus froid** qu'a cet endroit.

100 % gratuit, donnees ouvertes, deployable sur GitHub Pages.

## Pourquoi cette architecture (et pas un appel API par visiteur)

Open-Meteo gratuit compte **un appel par point de grille** (plafonds : 10 000/jour,
5 000/heure, 600/minute). Un champ mondial detaille (~50 000 points) coute donc
~50 000 appels : impossible. C'est ce qui declenchait les erreurs 429.

La solution est de **decoupler la donnee du visiteur** et d'utiliser une **source fichier** :

1. Un job planifie (**GitHub Actions**, gratuit) execute `scripts/fetch_gfs.py`
   chaque heure, cote serveur. Il telecharge **un seul fichier GRIB2** du modele
   **GFS de la NOAA** (domaine public, sans cle) depuis les miroirs open-data
   **AWS S3 / Google**, en lisant l'index `.idx` pour ne telecharger que le message
   GRIB "temperature 2 m" (~250 Ko), et ecrit `data/temps.json` : tout le globe au pas de
   **0,5°** en un seul telechargement, **sans quota par point**.
2. Le site (`index.html`) charge ce **fichier statique unique**. Tous les visiteurs
   lisent le meme fichier -> **aucun appel meteo par visiteur, aucun rate-limit**.
3. Le rendu heatmap est calcule dans le navigateur (interpolation bilineaire +
   reprojection Mercator) et recolore **instantanement** au changement de lieu/mode.

## Deploiement (5 min)

1. Cree un depot GitHub, pousse ces fichiers (`index.html`, `scripts/`, `.github/`).
2. **Settings -> Pages -> Source : GitHub Actions**.
3. Onglet **Actions** -> lance « Update temperature grid & deploy » (*Run workflow*)
   pour generer `data/temps.json` tout de suite.
4. Le site est en ligne ; les donnees se rafraichissent chaque heure.

> Le cron GitHub n'est pas a la minute pres et peut etre suspendu apres ~60 jours
> d'inactivite (un commit le reactive).

## Test en local

```bash
pip install numpy xarray cfgrib eccodes      # binaires GRIB inclus, rien a installer en plus
python scripts/fetch_gfs.py                  # ecrit data/temps.json
python -m http.server                        # puis ouvrir http://localhost:8000
```

Sans `data/temps.json`, l'app bascule sur une **grille de demonstration** recoltee en
direct via Open-Meteo (grossiere, une seule fois) juste pour visualiser le rendu.

## Reglages

`scripts/fetch_gfs.py` :
- `RES_TAG = "0p50"` (0,5°, conseille). Passer a `"0p25"` pour 0,25° et `STEP_H = 1`
  (4x plus de points, fichier ~4 Mo) si tu veux encore plus fin.
- `LAT_MIN / LAT_MAX` : zone couverte (poles ignores par defaut).

`index.html` : `cap` (ecart °C de saturation des couleurs), `rasterW` (finesse du
canvas de rendu), palettes `GRAD`, opacite via le curseur « Intensite ».

## Choix du champ « maintenant »

GFS publie un nouveau run toutes les 6 h (00/06/12/18 UTC), disponible ~5 h plus tard.
Le script choisit le run le plus recent disponible **et** l'echeance dont l'heure de
validite tombe au plus pres de l'instant present (donc un champ ~ a l'heure, pas un
run vieux de 6 h). C'est une sortie de modele (~9-25 km), pas une mesure de station.

## Donnees, attribution, limites

- **Meteo** : [GFS / NOAA NCEP](https://www.nco.ncep.noaa.gov/pmb/products/gfs/),
  lu sur les miroirs open-data AWS S3 (`noaa-gfs-bdp-pds`) / Google, fichiers GRIB2
  statiques avec index `.idx`. Donnees du gouvernement americain, domaine public.
- **Fond de carte** : [CARTO](https://carto.com) Dark Matter sur tuiles OpenStreetMap.
- **Geocodage / autocomplete** : [Photon](https://photon.komoot.io) (Komoot, base OSM).

Les instances publiques de **Photon**, **CARTO** et **NOMADS** ont des conditions
d'« usage raisonnable ». Pour un trafic eleve :
- Photon est open source et auto-hebergeable (Docker), comme Nominatim.
- Tuiles : prevoir une cle (Stadia, MapTiler) ou auto-heberger (Protomaps/OpenMapTiles).
- Le job ne fait qu'**un petit telechargement/heure** (~250 Ko) depuis S3, sans cle
  ni rate-limit ; aucune charge cote visiteur.

Aucune brique ne necessite de cle pour demarrer.
