"""Build the day/night images for the web demo (docs/demo-app/scenarios/dn/).

For each city and season this cuts the *same* 51.2 km square out of

  day    the least cloudy Sentinel-2 L2A scene of that season (true colour, 80 m/px), and
  night  NASA Black Marble (VIIRS day/night band, daily gap-filled and BRDF-corrected
         product, served by GIBS at ~1 km) for the date of that Sentinel-2 acquisition,

both on the same UTM grid, so the two are congruent and flipping between them lines up.

Sentinel-2 is optical and passes at ~10:30 local time, so it has no night scenes of its own;
Black Marble is the night half.

Requires:  pip install rasterio numpy pillow
Usage:     python scripts/make_daynight_demo.py [outdir]
Network:   sentinel-cogs.s3.us-west-2.amazonaws.com and gibs.earthdata.nasa.gov

Contains modified Copernicus Sentinel data 2024.  NASA Black Marble imagery via GIBS / Worldview
(NASA EOSDIS).
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import reproject, transform as warp_transform, transform_bounds
from rasterio.windows import from_bounds

sys.path.insert(0, str(Path(__file__).parent))
import make_s2_demo as s2                                     # scene search helpers

s2.HALF = 25600                                               # 51.2 km window -> 80 m/px at 640 px
SIZE = 640
GIBS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LAYER = "VIIRS_SNPP_GapFilled_BRDF_Corrected_DayNightBand_Radiance"
GIBS_PX = 0.0088                                              # ~1 km in degrees: what GIBS really serves for this layer

# night-lights palette: black -> deep blue -> amber -> warm white
_STOPS = [(0.00, (3, 5, 14)), (0.12, (12, 18, 46)), (0.30, (92, 52, 40)), (0.55, (230, 120, 30)),
          (0.80, (255, 190, 90)), (1.00, (255, 244, 205))]


def colorize(v: np.ndarray) -> np.ndarray:
    xs = [s[0] for s in _STOPS]
    return np.dstack([np.interp(v, xs, [s[1][c] for s in _STOPS]) for c in range(3)]).astype("uint8")


def fetch_gibs(bbox, date: dt.date):
    """Greyscale GIBS render of the bbox, as (array, transform).  Tries nearby dates if one is missing."""
    w, s, e, n = bbox
    width, height = int(np.ceil((e - w) / GIBS_PX)), int(np.ceil((n - s) / GIBS_PX))
    for off in (0, 1, -1, 2, -2, 3, -3):
        d = date + dt.timedelta(days=off)
        url = (f"{GIBS}?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS={LAYER}&SRS=EPSG:4326"
               f"&BBOX={w},{s},{e},{n}&WIDTH={width}&HEIGHT={height}&FORMAT=image/tiff&TIME={d.isoformat()}")
        with urllib.request.urlopen(url, timeout=120) as r:
            body, ctype = r.read(), r.headers.get("content-type", "")
        if "tiff" not in ctype:
            continue
        with rasterio.MemoryFile(body) as mf, mf.open() as ds:
            return ds.read(1).astype("float32"), ds.transform, d
    raise RuntimeError(f"no Black Marble within 3 days of {date}")


def day_image(tile, scene, lon, lat):
    with rasterio.open(s2.href(tile, scene, "TCI")) as ds:
        left, bottom, right, top = s2.aoi_bounds(ds.crs, lon, lat)
        win = from_bounds(left, bottom, right, top, ds.transform)
        rgb = ds.read(window=win, out_shape=(3, SIZE, SIZE), resampling=Resampling.average)
        return np.moveaxis(rgb, 0, -1), ds.crs, (left, bottom, right, top)


def night_image(crs, bounds, date):
    """Black Marble warped onto exactly the same UTM grid as the day image."""
    w, s, e, n = transform_bounds(crs, "EPSG:4326", *bounds, densify_pts=21)
    pad = 2 * GIBS_PX
    src, src_tf, used = fetch_gibs((w - pad, s - pad, e + pad, n + pad), date)
    dst = np.zeros((SIZE, SIZE), "float32")
    reproject(src, dst, src_transform=src_tf, src_crs="EPSG:4326",
              dst_transform=transform_from_bounds(*bounds, SIZE, SIZE), dst_crs=crs,
              resampling=Resampling.cubic)
    floor = 13.0                                              # GIBS paints "no light" at 7..13 depending on the date
    v = np.clip((dst - floor) / (255.0 - floor), 0, 1) ** 2.0     # GIBS saturates the cores; keep the suburbs readable
    return colorize(v), used


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for city, lon, lat, tiles in s2.CITIES:
        for season, months in s2.SEASONS.items():
            best = s2.pick(city, lon, lat, tiles, season, months)
            if best is None:
                sys.exit(f"no usable scene for {city} {season}")
            frac, tile, scene = best
            d = re.search(r"_(\d{4})(\d{2})(\d{2})_", scene).groups()
            date = dt.date(*map(int, d))
            rgb, crs, bounds = day_image(tile, scene, lon, lat)
            night, used = night_image(crs, bounds, date)
            Image.fromarray(rgb).save(out / f"{city}_{season}_day.jpg", quality=82, optimize=True)
            Image.fromarray(night).save(out / f"{city}_{season}_night.jpg", quality=84, optimize=True)
            print(f"{city:8s} {season:7s} {scene}  cloud {frac:5.1%}  black marble {used}")
            meta[f"{city}/{season}"] = {"scene": scene, "date": date.isoformat(), "tile": tile,
                                        "cloud": round(frac * 100, 1), "night_date": used.isoformat()}
    (out / "scenes.json").write_text(json.dumps(meta, indent=1))
    print(f"\n{len(meta) * 2} images -> {out}/")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo-app/scenarios/dn"))
