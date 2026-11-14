// Scenario: the same four cities by day and by night, from space.
//
//   day    Sentinel-2 true colour, the least cloudy scene of each season in 2024
//   night  NASA Black Marble (VIIRS day/night band) for the same date
//
// Both are cut to the same 32 km square on the same grid, so flipping between day and night
// lines up pixel for pixel. Sentinel-2 is optical and passes at ~10:30 local time, so it has no
// night scenes of its own; Black Marble supplies them. Rebuild with scripts/make_daynight_demo.py.
(() => {
  const axes = [
    { name: "city",   key: "c", values: ["newyork", "london", "paris", "berlin"] },
    { name: "season", key: "s", values: ["winter", "spring", "summer", "autumn"] },
    { name: "time",   key: "t", values: ["day", "night"] },
  ];
  const SIZE = 640;
  // [Sentinel-2 acquisition date, cloud cover over the window in %, Black Marble date]
  const META = {
 "newyork/winter": [
  "2024-01-02",
  0.0,
  "2024-01-02"
 ],
 "newyork/spring": [
  "2024-05-24",
  0.0,
  "2024-05-24"
 ],
 "newyork/summer": [
  "2024-08-27",
  1.7,
  "2024-08-27"
 ],
 "newyork/autumn": [
  "2024-10-23",
  0.0,
  "2024-10-23"
 ],
 "london/winter": [
  "2024-01-26",
  0.0,
  "2024-01-26"
 ],
 "london/spring": [
  "2024-05-20",
  0.0,
  "2024-05-20"
 ],
 "london/summer": [
  "2024-07-29",
  0.1,
  "2024-07-30"
 ],
 "london/autumn": [
  "2024-11-26",
  0.0,
  "2024-11-26"
 ],
 "paris/winter": [
  "2024-01-05",
  32.4,
  "2024-01-05"
 ],
 "paris/spring": [
  "2024-05-09",
  17.1,
  "2024-05-09"
 ],
 "paris/summer": [
  "2024-08-12",
  0.0,
  "2024-08-12"
 ],
 "paris/autumn": [
  "2024-11-28",
  0.0,
  "2024-11-28"
 ],
 "berlin/winter": [
  "2024-01-08",
  0.0,
  "2024-01-08"
 ],
 "berlin/spring": [
  "2024-05-15",
  0.0,
  "2024-05-15"
 ],
 "berlin/summer": [
  "2024-07-21",
  0.0,
  "2024-07-21"
 ],
 "berlin/autumn": [
  "2024-10-24",
  0.0,
  "2024-10-24"
 ]
};
  const imgs = new Map();
  const load = url => new Promise((res, rej) => { const i = new Image(); i.onload = () => res(i); i.onerror = () => rej(new Error("missing " + url)); i.src = url; });

  JUXT_SCENARIOS.push({
    id: "daynight", title: "Earth by day and night", axes, size: [SIZE, SIZE],
    template: "earth/{city}_{season}_{time}.jpg",
    blurb: "New York, London, Paris and Berlin from orbit: Sentinel-2 true colour by day, NASA Black Marble by night, on the same 32 km grid. Black Marble is a coarse (~1 km) product, so the night view is a soft glow: flip with Space to see where each city is lit and where the fields, forests and water begin.",
    hint: `Tap <kbd>t</kbd> for day ↔ night. The night data is coarse and clips the brightest cores, so watch the <em>edges</em> of each city. <kbd>s</kbd> steps the season, <kbd>c</kbd> the city.`,
    credit: "Day: contains modified Copernicus Sentinel data 2024 (Sentinel-2 L2A, Element 84 / AWS Open Data). Night: NASA Black Marble, VIIRS daily gap-filled radiance, via NASA GIBS / Worldview (EOSDIS), shown with a colour stretch. 32 km window.",
    async load(progress) {
      if (imgs.size) return;
      const all = JUXT.product(axes); let n = 0;
      await Promise.all(all.map(async c => {
        const im = await load(`scenarios/dn/${c[0]}_${c[1]}_${c[2]}.jpg`);
        const [date, cloud, nightDate] = META[`${c[0]}/${c[1]}`];
        const cv = JUXT.canvas(SIZE, SIZE), g = cv.getContext("2d");
        g.drawImage(im, 0, 0, SIZE, SIZE);
        JUXT.strip(g, SIZE, `city=${c[0]}  season=${c[1]}  time=${c[2]}`, 26);
        g.fillStyle = "rgba(0,0,0,.65)"; g.fillRect(0, SIZE - 22, SIZE, 22);
        g.fillStyle = "#d8d8d8"; g.font = "12px 'Courier New', monospace"; g.textBaseline = "middle";
        g.fillText(c[2] === "day" ? `${date}   Sentinel-2 true colour   cloud ${cloud}%` : `${nightDate}   NASA Black Marble (VIIRS)`, 8, SIZE - 10);
        imgs.set(c.join("|"), cv); progress(++n, all.length);
      }));
    },
    get: coords => imgs.get(coords.join("|")),
  });
})();
