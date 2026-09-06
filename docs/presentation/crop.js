const sharp = require('sharp');
const U = '/root/.claude/uploads/0f9403ad-2c87-56ec-be24-a8bf64450f17/';
const S = 1220 / 899;
const px = (v) => Math.round(v * S);
const BG = { r: 15, g: 20, b: 14 };                  // sampled from the screenshots themselves
const shots = [
  { id: 'a066c06d', out: 'assets/mesh1.png', bottom: 1420, legend: false },
  { id: 'a935d121', out: 'assets/mesh2.png', bottom: 1265, legend: false },
  { id: 'dd318cc3', out: 'assets/mesh3.png', bottom: 1600, legend: true  },  // legend overlaps this one
  { id: 'e645e19f', out: 'assets/mesh4.png', bottom: 960,  legend: false },
];
const TOP = px(215), X = px(18), W = px(864), CANVAS_H = px(1385);

(async () => {
  for (const s of shots) {
    const h = px(s.bottom) - TOP;
    let buf = await sharp(U + s.id + '-image.jpg')
      .extract({ left: X, top: TOP, width: W, height: h }).toBuffer();
    if (s.legend) {                                   // paint out the on-screen legend card
      const lx = px(620) - X, ly = px(1488) - TOP;
      const patch = await sharp({ create: { width: W - lx, height: h - ly, channels: 3, background: BG } }).png().toBuffer();
      buf = await sharp(buf).composite([{ input: patch, left: lx, top: ly }]).toBuffer();
    }
    await sharp({ create: { width: W, height: CANVAS_H, channels: 3, background: BG } })
      .composite([{ input: buf, top: 0, left: 0 }]).png().toFile(s.out);
  }
  console.log('crops written');
})();
