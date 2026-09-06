const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const sharp = require('sharp');
const fa = require('react-icons/fa6');
const fs = require('fs');

const WANT = ['FaArrowRight', 'FaArrowsRotate', 'FaBan', 'FaBatteryFull',
  'FaBolt', 'FaBrain', 'FaBuilding', 'FaChartLine',
  'FaCheck', 'FaCircleNodes', 'FaClock', 'FaCloudArrowUp',
  'FaDroplet', 'FaEuroSign', 'FaEye', 'FaFish',
  'FaFlaskVial', 'FaGears', 'FaHouse', 'FaLeaf',
  'FaLock', 'FaMicrochip', 'FaMobileScreenButton', 'FaPlug',
  'FaQrcode', 'FaRoute', 'FaSeedling', 'FaShieldHalved',
  'FaSnowflake', 'FaSolarPanel', 'FaTemperatureHalf', 'FaTowerBroadcast',
  'FaTractor', 'FaTriangleExclamation', 'FaTruckFast'];

if (!fs.existsSync('assets/icons')) fs.mkdirSync('assets/icons', { recursive: true });
(async () => {
  const missing = [];
  for (const name of WANT) {
    const Comp = fa[name];
    if (!Comp) { missing.push(name); continue; }
    for (const [suffix, color] of [['w','#FFFFFF'], ['d','#0F2B22'], ['s','#F0B429']]) {
      const svg = renderToStaticMarkup(React.createElement(Comp, { color, size: 192 }));
      await sharp(Buffer.from(svg)).resize(192, 192, { fit: 'contain', background: { r:0,g:0,b:0,alpha:0 } })
        .png().toFile(`assets/icons/${name}_${suffix}.png`);
    }
  }
  console.log('missing:', missing.length ? missing.join(', ') : 'none');
})();
