// Scenario: real Sentinel-2 imagery — four cities × four seasons × four image layers.
//
// The images were cut out of the least cloudy Sentinel-2 L2A scene of each season (2024) over each
// city; scripts/make_s2_demo.py rebuilds them from the open Sentinel-2 archive on AWS.
//
// Sentinel-2 is an optical sensor that passes at about 10:30 local time, so there is no "night"
// image to be had. The third axis is therefore the layer: the same scene, four ways of looking at it.
(() => {
  const axes = [
    { name: "city",   key: "c", values: ["newyork", "london", "paris", "berlin"] },
    { name: "season", key: "s", values: ["winter", "spring", "summer", "autumn"] },
    { name: "layer",  key: "l", values: ["true-color", "false-color", "ndvi", "swir"] },
  ];
  const SIZE = 640;
  // [acquisition date, cloud cover over the 12.8 km window in %, scene id]
  const META = {
 "newyork/winter": [
  "2024-01-02",
  0.0,
  "S2B_18TWL_20240102_0_L2A"
 ],
 "newyork/spring": [
  "2024-05-01",
  0.0,
  "S2B_18TWL_20240501_0_L2A"
 ],
 "newyork/summer": [
  "2024-08-27",
  3.8,
  "S2A_18TWL_20240827_0_L2A"
 ],
 "newyork/autumn": [
  "2024-10-23",
  0.0,
  "S2A_18TWL_20241023_0_L2A"
 ],
 "london/winter": [
  "2024-01-16",
  0.0,
  "S2A_30UXC_20240116_0_L2A"
 ],
 "london/spring": [
  "2024-05-20",
  0.0,
  "S2B_30UXC_20240520_0_L2A"
 ],
 "london/summer": [
  "2024-07-31",
  0.0,
  "S2A_30UXC_20240731_0_L2A"
 ],
 "london/autumn": [
  "2024-11-26",
  0.0,
  "S2B_30UXC_20241126_0_L2A"
 ],
 "paris/winter": [
  "2024-01-05",
  14.5,
  "S2B_31UDQ_20240105_0_L2A"
 ],
 "paris/spring": [
  "2024-05-09",
  1.1,
  "S2A_31UDQ_20240509_0_L2A"
 ],
 "paris/summer": [
  "2024-08-05",
  0.0,
  "S2B_31UDQ_20240805_0_L2A"
 ],
 "paris/autumn": [
  "2024-10-16",
  0.0,
  "S2A_31UDQ_20241016_0_L2A"
 ],
 "berlin/winter": [
  "2024-02-25",
  0.0,
  "S2B_33UUU_20240225_0_L2A"
 ],
 "berlin/spring": [
  "2024-05-02",
  0.0,
  "S2B_33UUU_20240502_0_L2A"
 ],
 "berlin/summer": [
  "2024-07-21",
  0.0,
  "S2B_33UUU_20240721_0_L2A"
 ],
 "berlin/autumn": [
  "2024-10-24",
  0.0,
  "S2A_33UUU_20241024_0_L2A"
 ]
};
  const LAYER_NOTE = {
    "true-color": "B4 B3 B2", "false-color": "B8 B4 B3 (vegetation = red)",
    "ndvi": "(B8-B4)/(B8+B4)", "swir": "B12 B8A B4 (built-up = purple/brown)",
  };
  const imgs = new Map();
  const load = url => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("missing " + url)); i.src = url; });

  JUXT_SCENARIOS.push({
    id: "sentinel2", title: "Sentinel-2 satellite imagery", axes, size: [SIZE, SIZE],
    template: "s2/{city}_{season}_{layer}.jpg",
    blurb: "Real Copernicus Sentinel-2 scenes of New York, London, Paris and Berlin in every season, each in four band combinations. Flip layers to see vegetation, water and built-up area separate; flip seasons to watch the green-up.",
    hint: `Tap <kbd>l</kbd> to cycle the layers, <kbd>s</kbd> for the season, <kbd>c</kbd> for the city. Zoom with <kbd>Ctrl</kbd>+wheel and drag — the pan survives flipping, which is what juxt is for.`,
    credit: "Contains modified Copernicus Sentinel data 2024. Sentinel-2 L2A cloud-optimised GeoTIFFs via Element 84 / AWS Open Data. 12.8 km window per city, 20 m/px.",
    async load(progress) {
      if (imgs.size) return;
      const all = JUXT.product(axes); let n = 0;
      await Promise.all(all.map(async c => {
        const im = await load(`scenarios/s2/${c[0]}_${c[1]}_${c[2]}.jpg`);
        const [date, cloud] = META[`${c[0]}/${c[1]}`];
        const cv = JUXT.canvas(SIZE, SIZE), g = cv.getContext("2d");
        g.drawImage(im, 0, 0, SIZE, SIZE);
        JUXT.strip(g, SIZE, `city=${c[0]}  season=${c[1]}  layer=${c[2]}`, 26);
        g.fillStyle = "rgba(0,0,0,.65)"; g.fillRect(0, SIZE - 22, SIZE, 22);
        g.fillStyle = "#d8d8d8"; g.font = "12px 'Courier New', monospace"; g.textBaseline = "middle";
        g.fillText(`${date}   cloud ${cloud}%   ${LAYER_NOTE[c[2]]}`, 8, SIZE - 10);
        imgs.set(c.join("|"), cv); progress(++n, all.length);
      }));
    },
    get: coords => imgs.get(coords.join("|")),
  });
})();
