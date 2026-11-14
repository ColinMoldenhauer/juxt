"""Build the Sentinel-2 images for the web demo (docs/demo-app/scenarios/s2/).

For each city and season, pick the least cloudy Sentinel-2 L2A scene over the
city from the open "sentinel-cogs" bucket on AWS (Element 84's cloud-optimised
GeoTIFFs), cut the same 12.8 km square out of it, and render four layers:

    true-color   B04 B03 B02
    false-color  B08 B04 B03   (vegetation shows up red)
    ndvi         (B08 - B04) / (B08 + B04), brown -> green
    swir         B12 B8A B04   (built-up area, bare soil and snow separate well)

Requires:  pip install rasterio numpy pillow
Usage:     python scripts/make_s2_demo.py [outdir]

Sentinel-2 is optical and passes at ~10:30 local time, so there is no night
scene to be had; that is why this scenario's third axis is the layer.

Contains modified Copernicus Sentinel data 2024.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.warp import transform as warp_transform
from rasterio.windows import from_bounds

BUCKET = "https://sentinel-cogs.s3.us-west-2.amazonaws.com"
PREFIX = "sentinel-s2-l2a-cogs"
SIZE = 640                     # output pixels
HALF = 6400                    # half the AOI edge, metres  (-> 20 m / pixel)

# name, lon, lat, candidate tiles (first one that covers the AOI wins)
CITIES = [
    ("newyork", -73.985, 40.750, ["18TWL"]),
    ("london",   -0.100, 51.510, ["30UXC", "31UCT"]),
    ("paris",     2.350, 48.857, ["31UDQ"]),
    ("berlin",   13.405, 52.520, ["33UUU"]),
]
SEASONS = {
    "winter": [(2024, 1), (2024, 2)],
    "spring": [(2024, 4), (2024, 5)],
    "summer": [(2024, 7), (2024, 8)],
    "autumn": [(2024, 10), (2024, 11)],
}
CLOUDY_SCL = (3, 8, 9, 10)     # cloud shadow, cloud medium/high, thin cirrus


def s3_list(prefix: str) -> list[str]:
    url = f"{BUCKET}/?list-type=2&delimiter=/&prefix={urllib.parse.quote(prefix)}"
    with urllib.request.urlopen(url, timeout=30) as r:
        root = ET.fromstring(r.read())
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    return [p.find("s:Prefix", ns).text.rstrip("/").rsplit("/", 1)[-1]
            for p in root.findall("s:CommonPrefixes", ns)]


def scenes(tile: str, year: int, month: int) -> list[str]:
    zone, band, sq = tile[:2], tile[2], tile[3:]
    return s3_list(f"{PREFIX}/{int(zone)}/{band}/{sq}/{year}/{month}/")


def href(tile: str, scene: str, asset: str) -> str:
    zone, band, sq = tile[:2], tile[2], tile[3:]
    y, m = re.search(r"_(\d{4})(\d{2})\d{2}_", scene).groups()
    return f"{BUCKET}/{PREFIX}/{int(zone)}/{band}/{sq}/{y}/{int(m)}/{scene}/{asset}.tif"


def aoi_bounds(crs, lon, lat):
    (x,), (y,) = warp_transform("EPSG:4326", crs, [lon], [lat])
    return x - HALF, y - HALF, x + HALF, y + HALF


def read(url: str, lon: float, lat: float, size: int = SIZE, resampling=Resampling.bilinear):
    """The AOI of one band as float32 reflectance (or the raw array for SCL)."""
    with rasterio.open(url) as ds:
        left, bottom, right, top = aoi_bounds(ds.crs, lon, lat)
        b = ds.bounds
        if left < b.left or bottom < b.bottom or right > b.right or top > b.top:
            return None
        win = from_bounds(left, bottom, right, top, ds.transform)
        return ds.read(1, window=win, out_shape=(size, size), resampling=resampling)


def cloud_fraction(tile, scene, lon, lat):
    scl = read(href(tile, scene, "SCL"), lon, lat, size=96, resampling=Resampling.nearest)
    if scl is None:
        return None
    if (scl == 0).mean() > 0.01:          # nodata: AOI sits on the swath edge
        return None
    return float(np.isin(scl, CLOUDY_SCL).mean())


def pick(city, lon, lat, tiles, season, months):
    best = None
    for tile in tiles:
        for year, month in months:
            for scene in scenes(tile, year, month):
                frac = cloud_fraction(tile, scene, lon, lat)
                if frac is None:
                    continue
                if best is None or frac < best[0]:
                    best = (frac, tile, scene)
        if best:
            break
    return best


def stretch(a, hi, gamma=0.8):
    return np.clip(a / hi, 0, 1) ** gamma


def rgb(*bands, hi=3000.0, gamma=0.8):
    return np.dstack([stretch(b, hi, gamma) for b in bands])


# brown -> yellow -> green -> dark green;  water / bare surfaces stay dark
_NDVI_STOPS = [(-0.2, (30, 60, 110)), (0.0, (120, 95, 70)), (0.15, (200, 180, 110)),
               (0.35, (150, 190, 70)), (0.6, (50, 150, 50)), (0.9, (0, 80, 30))]


def ndvi_color(nd):
    xs = [s[0] for s in _NDVI_STOPS]
    out = np.dstack([np.interp(nd, xs, [s[1][c] for s in _NDVI_STOPS]) for c in range(3)])
    return out / 255.0


def layers(tile, scene, lon, lat):
    g = lambda a, **kw: read(href(tile, scene, a), lon, lat, **kw).astype("float32")
    b02, b03, b04, b08 = g("B02"), g("B03"), g("B04"), g("B08")
    b8a, b12 = g("B8A"), g("B12")
    nd = (b08 - b04) / np.maximum(b08 + b04, 1)
    return {
        "true-color": rgb(b04, b03, b02, hi=3000),
        "false-color": rgb(b08, b04, b03, hi=5500, gamma=0.9),
        "ndvi": ndvi_color(nd),
        "swir": rgb(b12, b8a, b04, hi=4500, gamma=0.9),
    }


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    meta = {}
    for city, lon, lat, tiles in CITIES:
        for season, months in SEASONS.items():
            best = pick(city, lon, lat, tiles, season, months)
            if best is None:
                sys.exit(f"no usable scene for {city} {season}")
            frac, tile, scene = best
            date = re.search(r"_(\d{8})_", scene).group(1)
            print(f"{city:8s} {season:7s} {scene}  cloud in AOI {frac:5.1%}")
            meta[f"{city}/{season}"] = {
                "scene": scene, "date": f"{date[:4]}-{date[4:6]}-{date[6:]}",
                "tile": tile, "cloud": round(frac * 100, 1),
            }
            for name, img in layers(tile, scene, lon, lat).items():
                Image.fromarray((img * 255).astype("uint8")).save(
                    out / f"{city}_{season}_{name}.jpg", quality=80, optimize=True)
    (out / "scenes.json").write_text(json.dumps(meta, indent=1))
    print(f"\n{len(meta) * 4} images -> {out}/")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/demo-app/scenarios/s2"))
