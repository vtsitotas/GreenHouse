const pptxgen = require('pptxgenjs');
const p = new pptxgen();
p.layout = 'LAYOUT_WIDE';                 // 13.333 x 7.5
p.author = 'GreenHouse';
p.title  = 'GreenHouse — Παρουσίαση προϊόντος';

const W = 13.333, H = 7.5, M = 0.7, CW = W - 2 * M;
const INK='0F2B22', INK2='17392C', MOSS='7FA650', LEAF='2C5F2D', SUN='F0B429';
const WHT='FFFFFF', TINT='F1F5EF', TINT2='E4EDE0', TXT='16241C', MUT='5F7268',
      DIM='9FB8A6', PALE='CFE0D2', RUST='B4472F';
const HF='Cambria', BF='Calibri';
const ic = (n, v='w') => 'assets/icons/' + n + '_' + v + '.png';

// ---------- helpers (fresh option objects every call) ----------
const shadow = () => ({ type:'outer', color:'0F2B22', blur:10, offset:2, angle:90, opacity:0.10 });

function card(s, x, y, w, h, fill = TINT) {
  s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill:{color:fill}, line:{color:fill,width:0},
    rectRadius:0.10, shadow: shadow() });
}
function badge(s, x, y, icon, d = 0.62, ring = LEAF, tone = 'w') {
  s.addShape(p.ShapeType.ellipse, { x, y, w:d, h:d, fill:{color:ring}, line:{color:ring,width:0} });
  const p2 = d * 0.46;
  s.addImage({ path: ic(icon, tone), x: x + (d - p2) / 2, y: y + (d - p2) / 2, w:p2, h:p2 });
}
function kicker(s, t, color = MOSS) {
  s.addText(t, { x:M, y:0.42, w:CW, h:0.3, fontSize:11.5, bold:true, color, fontFace:BF,
    charSpacing:2.2, isTextBox:true, margin:0 });
}
function title(s, t, color = INK, size = 33, h = 1.15) {
  s.addText(t, { x:M, y:0.75, w:CW, h, fontSize:size, bold:true, color, fontFace:HF,
    isTextBox:true, margin:0, valign:'top', lineSpacing: size * 1.18 });
}
function body(s, t, o) {
  s.addText(t, Object.assign({ fontSize:13.5, color:MUT, fontFace:BF, isTextBox:true,
    margin:0, valign:'top', lineSpacing:19 }, o));
}
function bandDark(s, x, y, w, h, t, size = 14) {
  s.addShape(p.ShapeType.roundRect, { x, y, w, h, fill:{color:INK}, line:{color:INK,width:0}, rectRadius:0.10 });
  s.addText(t, { x:x+0.42, y:y+0.14, w:w-0.84, h:h-0.28, fontSize:size, color:PALE, fontFace:BF,
    isTextBox:true, margin:0, valign:'middle', lineSpacing:size*1.42 });
}
function arrow(s, x, y, d = 0.3) { s.addImage({ path: ic('FaArrowRight','s'), x, y, w:d, h:d }); }
function footnote(s, t, y = 7.02) {
  s.addText(t, { x:M, y, w:CW, h:0.3, fontSize:9.5, color:MUT, fontFace:BF, italic:true,
    isTextBox:true, margin:0 });
}
// decorative node-and-link motif for dark slides
function motif(s, ox, oy, sc = 1, col = '265444') {
  const n = [[0,0.9],[1.5,0],[1.35,1.95],[3.0,1.1],[2.7,3.0],[0.5,2.6],[4.1,2.3]];
  const e = [[0,1],[0,5],[1,2],[2,3],[3,4],[3,6],[5,2]];
  e.forEach(([a,b]) => s.addShape(p.ShapeType.line, {
    x:ox+n[a][0]*sc, y:oy+n[a][1]*sc, w:(n[b][0]-n[a][0])*sc, h:(n[b][1]-n[a][1])*sc,
    line:{ color:col, width:1.25 } }));
  n.forEach((q,i) => { const d = (i===1?0.20:0.13)*sc;
    s.addShape(p.ShapeType.ellipse, { x:ox+q[0]*sc-d/2, y:oy+q[1]*sc-d/2, w:d, h:d,
      fill:{ color: i===1 ? SUN : MOSS }, line:{ color: i===1 ? SUN : MOSS, width:0 } }); });
}

/* ============================ 1 · TITLE ============================ */
{
  const s = p.addSlide();
  s.background = { color: INK };
  motif(s, 8.15, 1.55, 1.02);
  s.addText('ΔΙΠΛΩΜΑΤΙΚΗ ΕΡΓΑΣΙΑ · ΠΑΡΟΥΣΙΑΣΗ ΠΡΟΪΟΝΤΟΣ', {
    x:0.9, y:1.95, w:7.4, h:0.32, fontSize:11.5, bold:true, color:SUN, fontFace:BF,
    charSpacing:2.4, isTextBox:true, margin:0 });
  s.addText('GreenHouse', { x:0.86, y:2.32, w:7.4, h:1.0, fontSize:54, bold:true, color:WHT,
    fontFace:HF, isTextBox:true, margin:0 });
  s.addText('Το θερμοκήπιο που δεν χρειάζεται\nνα το προσέχεις.', {
    x:0.9, y:3.42, w:7.3, h:1.35, fontSize:25, color:PALE, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:36 });
  s.addText('Αυτο-οργανούμενο δίκτυο αισθητήρων με μπαταρία  ·  Τοπική νοημοσύνη, χωρίς cloud  ·  Από το θερμοκήπιο στο φυτώριο και στο χωράφι', {
    x:0.9, y:4.92, w:7.3, h:0.7, fontSize:12.5, color:DIM, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:19 });
  s.addText('Σεπτέμβριος 2026', { x:0.9, y:6.5, w:5, h:0.3, fontSize:11, color:'6E8A78',
    fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~40 δευτ. Άνοιγμα: «Θα σας δείξω ένα σύστημα που φροντίζει ένα θερμοκήπιο μόνο του — και το γιατί έχει σημασία πολύ πέρα από τα φυτά.» Πες ότι θα πάμε: πρόβλημα → πώς δουλεύει → τι το κάνει διαφορετικό → πού πάει.');
}

/* ============================ 2 · ΤΟ ΠΡΟΒΛΗΜΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΤΟ ΠΡΟΒΛΗΜΑ');
  title(s, 'Η ζημιά γίνεται τη νύχτα που δεν κοίταξε κανείς.');
  const stats = [
    ['FaDroplet', '80–85%', 'του νερού που καταναλώνει η Ελλάδα πηγαίνει στην άρδευση — με το ρολόι, όχι με τη μέτρηση.'],
    ['FaSeedling', '~66.000', 'στρέμματα θερμοκηπίων στην Ελλάδα. Τα περισσότερα ελέγχονται ακόμη με το μάτι.'],
    ['FaSnowflake', 'Μία νύχτα', 'παγετού αρκεί για να μηδενίσει μια σεζόν. Η ειδοποίηση πρέπει να έρθει πριν.'],
  ];
  stats.forEach(([i, big, txt], k) => {
    const x = M + k * 4.08;
    card(s, x, 2.35, 3.78, 2.82);
    badge(s, x + 0.4, 2.68, i);
    s.addText(big, { x:x+0.4, y:3.44, w:3.0, h:0.62, fontSize:30, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, txt, { x:x+0.4, y:4.12, w:3.02, h:0.95, fontSize:12.5, lineSpacing:17.5 });
  });
  bandDark(s, M, 5.42, CW, 1.05,
    'Ο καλλιεργητής μαθαίνει το πρόβλημα όταν το βλέπει στο φύλλο. Τότε είναι ήδη αργά — και η μέτρηση που θα το είχε προλάβει κόστιζε λίγα ευρώ.', 15);
  footnote(s, 'Πηγές: ΕΛΣΤΑΤ (θερμοκηπιακές εκτάσεις, 2018) · Global NEST Journal, Latinopoulos (άρδευση ως ποσοστό κατανάλωσης νερού).');
  s.addNotes('~45 δευτ. Μην διαβάσεις τα νούμερα — δείξε τα. Το μήνυμα: το πρόβλημα δεν είναι η άγνοια, είναι ο χρόνος. Κανείς δεν μπορεί να είναι στο θερμοκήπιο στις 4 τα ξημερώματα.');
}

/* ============================ 3 · ΓΙΑΤΙ ΔΕΝ ΕΧΕΙ ΛΥΘΕΙ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΓΙΑΤΙ ΔΕΝ ΕΧΕΙ ΛΥΘΕΙ ΗΔΗ');
  title(s, 'Τέσσερα εμπόδια κρατούν τον αυτοματισμό μακριά από το χωράφι');
  const rows = [
    ['FaCloudArrowUp', 'Συνδρομή για τα δικά σου δεδομένα', 'Τα εμπορικά συστήματα στέλνουν τις μετρήσεις σε ξένο cloud και χρεώνουν κάθε μήνα για να τις διαβάσεις.'],
    ['FaPlug', 'Καλώδιο και ρεύμα σε κάθε σημείο', 'Οι αυτοσχέδιες λύσεις θέλουν πρίζα και router παντού. Μέσα στην υγρασία, κάθε καλώδιο είναι βλάβη που περιμένει.'],
    ['FaBatteryFull', 'Το WiFi τρώει τη μπαταρία', 'Ένας κόμβος που σηκώνει WiFi αδειάζει σε ώρες. Η ενέργεια — όχι ο αισθητήρας — είναι το πραγματικό εμπόδιο.'],
    ['FaBan', 'Πέφτει η γραμμή, χάνεις το θερμοκήπιο', 'Όταν ο εγκέφαλος είναι στο cloud, μια διακοπή Internet σταματά και το πότισμα και τις ειδοποιήσεις.'],
  ];
  rows.forEach(([i, h, t], k) => {
    const x = M + (k % 2) * 6.12, y = 2.25 + Math.floor(k / 2) * 2.12;
    card(s, x, y, 5.82, 1.98);
    badge(s, x + 0.36, y + 0.32, i);
    s.addText(h, { x:x+1.16, y:y+0.28, w:4.4, h:0.58, fontSize:15, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0, lineSpacing:20 });
    body(s, t, { x:x+1.16, y:y+0.92, w:4.42, h:0.92, fontSize:12.5, lineSpacing:17.5 });
  });
  s.addText('Κανένα από τα τέσσερα δεν είναι νόμος της φύσης. Και τα τέσσερα είναι σχεδιαστικές επιλογές — που μπορούν να γίνουν αλλιώς.', {
    x:M, y:6.55, w:CW, h:0.5, fontSize:15, italic:true, color:INK, fontFace:BF,
    isTextBox:true, margin:0 });
  s.addNotes('~50 δευτ. Αυτή είναι η διαφάνεια που δικαιολογεί την ύπαρξη του έργου. Κλείσε με τη γραμμή στο κάτω μέρος — είναι η γέφυρα προς τη λύση.');
}

/* ============================ 4 · Η ΙΔΕΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'Η ΛΥΣΗ');
  title(s, 'Η ιδέα, σε μία διαφάνεια');
  const boxes = [
    ['FaCircleNodes', 'Κόμβοι μπαταρίας', 'Μετρούν θερμοκρασία, υγρασία αέρα και υγρασία εδάφους. Μιλούν απευθείας μεταξύ τους.'],
    ['FaTowerBroadcast', 'Γέφυρα', 'Τρία σύρματα προς τον υπολογιστή. Καμία σύνδεση, κανένας κωδικός επάνω της.'],
    ['FaMicrochip', 'Ο εγκέφαλος', 'Ένας υπολογιστής παλάμης μέσα στον χώρο: βάση δεδομένων, κανόνες, καιρός, ασφάλεια.'],
    ['FaMobileScreenButton', 'Η εφαρμογή', 'Ζώνες, χάρτης δικτύου, ιστορικό, κανόνες και ειδοποιήσεις — μέσα κι έξω από το κτήμα.'],
  ];
  boxes.forEach(([i, h, t], k) => {
    const x = M + k * 3.05;
    card(s, x, 2.35, 2.7, 2.75);
    badge(s, x + 0.34, 2.66, i);
    s.addText(h, { x:x+0.34, y:3.42, w:2.2, h:0.32, fontSize:13.5, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:x+0.34, y:3.82, w:2.1, h:1.2, fontSize:11.5, lineSpacing:16 });
    if (k < 3) arrow(s, x + 2.85, 3.45, 0.28);
  });
  bandDark(s, M, 5.35, CW, 1.15,
    'Κανένα cloud IoT platform. Καμία συνδρομή. Ο εγκέφαλος μένει μέσα στο θερμοκήπιο — και συνεχίζει να ποτίζει και να ειδοποιεί ακόμη κι όταν πέσει το Internet.', 15);
  s.addNotes('~50 δευτ. Διάβασε τη ροή αριστερά→δεξιά μία φορά, αργά. Το κάτω πλαίσιο είναι το κύριο μήνυμα της διαφάνειας.');
}

/* ============================ 5 · ΤΡΕΙΣ ΑΠΟΦΑΣΕΙΣ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΟΙ ΑΠΟΦΑΣΕΙΣ ΠΟΥ ΜΕΤΡΑΝΕ');
  title(s, 'Τρεις επιλογές που αλλάζουν τα πάντα');
  const cs = [
    ['FaBatteryFull', 'Χωρίς WiFi στους αισθητήρες', '330×', 'περισσότερη ζωή μπαταρίας από έναν κόμβο που μένει συνεχώς ξύπνιος. Ξυπνά, στέλνει, κοιμάται.'],
    ['FaArrowsRotate', 'Δίκτυο που στήνεται μόνο του', 'Μηδέν', 'καλώδια, router ή ρυθμίσεις μέσα στον χώρο. Οι κόμβοι βρίσκουν δρόμο μεταξύ τους και τον ξαναβρίσκουν αν χαθεί.'],
    ['FaEuroSign', 'Ο εγκέφαλος μένει στο κτήμα', '€0', 'συνδρομή τον μήνα, για πάντα. Τα δεδομένα σου δεν φεύγουν ποτέ από τον χώρο σου.'],
  ];
  cs.forEach(([i, h, big, t], k) => {
    const x = M + k * 4.08;
    card(s, x, 2.35, 3.78, 3.55);
    badge(s, x + 0.4, 2.68, i);
    s.addText(h, { x:x+0.4, y:3.46, w:3.0, h:0.66, fontSize:15, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0, lineSpacing:19 });
    s.addText(big, { x:x+0.4, y:4.18, w:3.0, h:0.68, fontSize:34, bold:true, color:LEAF,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:x+0.4, y:4.9, w:3.0, h:0.9, fontSize:12.5, lineSpacing:17.5 });
  });
  s.addText('Καμία από τις τρεις δεν κοστίζει παραπάνω. Είναι απλώς η σωστή απόφαση, παρμένη νωρίς.', {
    x:M, y:6.2, w:CW, h:0.44, fontSize:15, italic:true, color:INK, fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~50 δευτ. Οι τρεις στήλες αντιστοιχούν στα τρία από τα τέσσερα εμπόδια της προηγούμενης διαφάνειας. Πες το ρητά.');
}

/* ============================ 6 · ΜΠΑΤΑΡΙΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΕΝΕΡΓΕΙΑ');
  title(s, 'Ένας αισθητήρας που δεν τον αγγίζεις ποτέ');
  s.addText('205 ημέρες', { x:M, y:2.3, w:5.6, h:0.78, fontSize:40, bold:true, color:LEAF,
    fontFace:HF, isTextBox:true, margin:0 });
  body(s, 'με μία μικρή μπαταρία — χωρίς καθόλου ήλιο.', { x:M, y:3.06, w:5.6, h:0.34, fontSize:14, color:INK });
  const pts = [
    'Ξυπνά 2,5 δευτερόλεπτα κάθε 15 λεπτά. Είναι ενεργός το 0,28% της ζωής του.',
    'Με ένα μικρό ηλιακό πάνελ, ο χειμώνας της Αθήνας δίνει περίπου 60 φορές περισσότερη ενέργεια απ’ όση χρειάζεται.',
    'Στέλνει και τη δική του στάθμη μπαταρίας — το ξέρεις πριν σβήσει, όχι όταν λείψουν οι μετρήσεις.',
  ];
  s.addText(pts.map((t, i) => ({ text:t, options:{ bullet:true, breakLine: i < pts.length - 1 } })), {
    x:M, y:3.6, w:5.6, h:2.0, fontSize:13, color:MUT, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:18, paraSpaceAfter:9 });
  s.addChart(p.ChartType.bar, [{
      name: 'Ημέρες λειτουργίας', labels: ['Συνεχώς ξύπνιος', 'Ξυπνά μόνο όταν χρειάζεται'],
      values: [0.6, 205] }], {
    x:6.62, y:2.28, w:6.0, h:3.35, barDir:'bar', chartColors:[RUST, LEAF], barGapWidthPct:60,
    showTitle:true, title:'Αυτονομία με την ίδια μπαταρία (ημέρες)', titleFontSize:13,
    titleColor:INK, titleFontFace:BF, showLegend:false,
    showValue:true, dataLabelPosition:'outEnd', dataLabelFontSize:13, dataLabelColor:INK,
    dataLabelFontFace:BF, dataLabelFormatCode:'0.#',
    catAxisLabelColor:MUT, catAxisLabelFontSize:12, catAxisLabelFontFace:BF,
    valAxisLabelColor:MUT, valAxisLabelFontSize:10, valAxisLabelFontFace:BF,
    valGridLine:{ color:'DCE5D8', size:1 }, catGridLine:{ style:'none' },
    valAxisMinVal:0, valAxisMaxVal:220, chartArea:{ fill:{ color:WHT } } });
  footnote(s, 'Υπολογισμοί από τις σταθερές λειτουργίας του ίδιου του κόμβου: κύκλος 15 λεπτών, ~2,5 δευτ. ξύπνιος, μπαταρία 1500 mAh.', 6.9);
  s.addNotes('~55 δευτ. Η δυνατή ατάκα: «η μπαταρία δεν είναι λεπτομέρεια — είναι το προϊόν». Ένα σύστημα που θέλει αλλαγή μπαταριών κάθε βδομάδα δεν το χρησιμοποιεί κανείς τη δεύτερη σεζόν.');
}

/* ============================ 7 · ΑΠΟΔΕΙΞΗ (screenshots) ============================ */
{
  const s = p.addSlide();
  s.background = { color: INK };
  s.addText('ΠΡΑΓΜΑΤΙΚΕΣ ΟΘΟΝΕΣ ΑΠΟ ΤΗΝ ΕΦΑΡΜΟΓΗ  ·  ΕΠΑΛΗΘΕΥΜΕΝΟ ΣΕ ΠΡΑΓΜΑΤΙΚΟ ΥΛΙΚΟ', {
    x:M, y:0.42, w:CW, h:0.3, fontSize:11.5, bold:true, color:SUN, fontFace:BF,
    charSpacing:2.2, isTextBox:true, margin:0 });
  s.addText('Το ίδιο δίκτυο. Τέσσερις φορές. Καμία ρύθμιση.', {
    x:M, y:0.78, w:CW, h:0.62, fontSize:29, bold:true, color:WHT, fontFace:HF, isTextBox:true, margin:0 });
  const caps = ['Δύο απευθείας,\nένας μέσω γείτονα', 'Και οι τρεις\nμέσω ενός κόμβου',
                'Αλυσίδα — τρία\nάλματα ως τη γέφυρα', 'Και οι τρεις\nαπευθείας'];
  for (let k = 0; k < 4; k++) {
    const x = 0.78 + k * 3.06;
    s.addImage({ path:'assets/mesh' + (k + 1) + '.png', x, y:1.72, w:2.6, h:4.17 });
    s.addText(caps[k], { x, y:5.98, w:2.6, h:0.6, fontSize:11.5, color:DIM, fontFace:BF,
      align:'center', isTextBox:true, margin:0, lineSpacing:15 });
  }
  s.addText('Οι κόμβοι διαλέγουν μόνοι τους ποιος περνάει μέσα από ποιον, ανάλογα με το σήμα της στιγμής. Όταν ένας δρόμος χαλάει, το δίκτυο ξαναστήνεται χωρίς να το ζητήσει κανείς.', {
    x:M, y:6.72, w:CW, h:0.5, fontSize:13.5, color:PALE, fontFace:BF, align:'center',
    isTextBox:true, margin:0 });
  s.addNotes('~60 δευτ. ΚΟΡΥΦΑΙΑ ΔΙΑΦΑΝΕΙΑ. Δείξε τις τέσσερις εικόνες με τη σειρά και πες: ίδιοι κόμβοι, ίδιο δωμάτιο, καμία ρύθμιση — το δίκτυο βρήκε τέσσερις διαφορετικές τοπολογίες μόνο του. Αυτό δεν είναι προσομοίωση, είναι στιγμιότυπα από το κινητό.');
}

/* ============================ 8 · ΠΩΣ ΔΟΥΛΕΥΕΙ ΤΟ ΔΙΚΤΥΟ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΤΟ ΔΙΚΤΥΟ');
  title(s, 'Πώς βρίσκει τον δρόμο του — χωρίς να του τον πει κανείς');
  card(s, M, 2.3, 6.1, 2.55, TINT);
  s.addText('πολύ μακριά για απευθείας', { x:1.5, y:2.46, w:4.3, h:0.3, fontSize:11.5,
    color:RUST, fontFace:BF, italic:true, align:'center', isTextBox:true, margin:0 });
  s.addShape(p.ShapeType.line, { x:1.15, y:2.86, w:4.9, h:0,
    line:{ color:RUST, width:1.5, dashType:'dash' } });
  const nodes = [['Αισθητήρας', 1.05], ['Γείτονας', 2.95], ['Γέφυρα', 4.85]];
  nodes.forEach(([lab, x], i) => {
    s.addShape(p.ShapeType.roundRect, { x, y:3.35, w:1.5, h:0.8,
      fill:{ color: i === 2 ? LEAF : WHT }, line:{ color: i === 2 ? LEAF : 'CBD9C6', width:1 }, rectRadius:0.08 });
    s.addText(lab, { x, y:3.35, w:1.5, h:0.8, fontSize:12, bold:true,
      color: i === 2 ? WHT : INK, fontFace:BF, align:'center', valign:'middle', isTextBox:true, margin:0 });
    if (i < 2) arrow(s, x + 1.62, 3.6, 0.26);
  });
  s.addText('Το πακέτο περνάει από τον γείτονα. Ο γείτονας το κουβαλάει — δεν το διαβάζει.', {
    x:0.95, y:4.32, w:5.6, h:0.42, fontSize:12, color:MUT, fontFace:BF, isTextBox:true, margin:0, lineSpacing:16 });
  const steps = [
    ['FaTowerBroadcast', 'Ο καθένας λέει πόσο μακριά είναι', 'Η γέφυρα λέει «μηδέν βήματα». Όποιος την ακούει λέει «ένα». Όποιος ακούει εκείνον λέει «δύο».'],
    ['FaRoute', 'Διαλέγει τον κοντινότερο στη γέφυρα', 'Και του δίνει τις μετρήσεις του. Αυτό είναι όλη η δρομολόγηση — δεν υπάρχει κεντρικός σχεδιασμός.'],
    ['FaArrowsRotate', 'Αν ο δρόμος χαθεί, ξαναδιαλέγει', 'Μόνος του, μέσα σε δευτερόλεπτα. Κανένα ξαναστήσιμο, καμία επίσκεψη στο χωράφι.'],
  ];
  steps.forEach(([i, h, t], k) => {
    const y = 2.3 + k * 1.32;
    badge(s, 7.15, y, i, 0.56);
    s.addText(h, { x:7.9, y:y+0.02, w:4.7, h:0.3, fontSize:14, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:7.9, y:y+0.36, w:4.7, h:0.8, fontSize:12, lineSpacing:16.5 });
  });
  bandDark(s, M, 6.28, CW, 0.86,
    'Μόνο εγγεγραμμένες συσκευές γίνονται δεκτές ως αναμεταδότες, και οι μετρήσεις ταξιδεύουν κρυπτογραφημένες από άκρο σε άκρο.', 13.5);
  s.addNotes('~55 δευτ. Χρησιμοποίησε την εικόνα της σκυταλοδρομίας. Τόνισε ότι ο ενδιάμεσος κόμβος δεν διαβάζει το περιεχόμενο — αυτό είναι που κάνει το δίκτυο να κλιμακώνεται χωρίς όριο.');
}

/* ============================ 9 · ΑΠΟΦΑΣΙΖΕΙ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΑΥΤΟΜΑΤΙΣΜΟΣ');
  title(s, 'Δεν μετράει. Αποφασίζει.');
  const chips = ['ΑΙΣΘΑΝΕΤΑΙ', 'ΠΡΟΒΛΕΠΕΙ', 'ΑΠΟΦΑΣΙΖΕΙ', 'ΔΡΑ', 'ΕΙΔΟΠΟΙΕΙ'];
  chips.forEach((c, k) => {
    const x = M + k * 2.42;
    s.addShape(p.ShapeType.roundRect, { x, y:2.14, w:2.18, h:0.56, fill:{ color:TINT2 },
      line:{ color:TINT2, width:0 }, rectRadius:0.1 });
    s.addText(c, { x, y:2.14, w:2.18, h:0.56, fontSize:11.5, bold:true, color:INK, fontFace:BF,
      align:'center', valign:'middle', charSpacing:1, isTextBox:true, margin:0 });
    if (k < 4) arrow(s, x + 2.24, 2.31, 0.24);
  });
  const cs = [
    ['FaGears', 'Κανόνες που ορίζεις εσύ', '«Αν η υγρασία του εδάφους μείνει κάτω από 35% για δύο ώρες, άνοιξε το πότισμα.» Ο κανόνας ζει στο θερμοκήπιο και τρέχει ακόμη κι αν το κινητό σου είναι κλειστό.'],
    ['FaLeaf', 'Προφίλ φυτού ανά ζώνη', 'Οκτώ έτοιμα προφίλ — τομάτα, αγγούρι, πιπεριά, μελιτζάνα, μαρούλι, βασιλικός, φράουλα, παχύφυτα. Λες τι φύτεψες· τα σωστά όρια μπαίνουν μόνα τους.'],
    ['FaSnowflake', 'Παγετός πριν συμβεί', 'Η ειδοποίηση βγαίνει από την πρόγνωση, όχι από το θερμόμετρο. Προλαβαίνεις να καλύψεις — αντί να μετρήσεις τη ζημιά το πρωί.'],
  ];
  cs.forEach(([i, h, t], k) => {
    const x = M + k * 4.08;
    card(s, x, 3.1, 3.78, 2.72);
    badge(s, x + 0.4, 3.42, i);
    s.addText(h, { x:x+0.4, y:4.18, w:3.0, h:0.34, fontSize:14.5, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:x+0.4, y:4.58, w:3.02, h:1.14, fontSize:12, lineSpacing:16.5 });
  });
  s.addText('Ένα σύστημα που σου λέει ότι έχει 4 βαθμούς σού δίνει δουλειά. Ένα σύστημα που κλείνει την κουρτίνα σού δίνει χρόνο.', {
    x:M, y:6.2, w:CW, h:0.44, fontSize:15, italic:true, color:INK, fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~55 δευτ. Η διάκριση παρακολούθηση vs αυτοματισμός είναι το εμπορικό επιχείρημα. Τα προφίλ φυτών είναι το κομμάτι που κάνει το σύστημα κατανοητό σε κάποιον που δεν είναι μηχανικός.');
}

/* ============================ 10 · ΑΣΦΑΛΕΙΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΑΣΦΑΛΕΙΑ');
  title(s, 'Ένα θερμοκήπιο είναι υπολογιστής. Το αντιμετωπίσαμε έτσι.');
  s.addShape(p.ShapeType.roundRect, { x:M, y:2.3, w:5.5, h:3.6, fill:{ color:INK },
    line:{ color:INK, width:0 }, rectRadius:0.1, shadow: shadow() });
  badge(s, M + 0.45, 2.72, 'FaShieldHalved', 0.68, SUN, 'd');
  s.addText('Ένας παραβιασμένος αισθητήρας δεν ανοίγει το πότισμα.', {
    x:M+0.45, y:3.58, w:4.6, h:1.15, fontSize:18.5, bold:true, color:WHT, fontFace:HF,
    isTextBox:true, margin:0, lineSpacing:25 });
  s.addText('Κάθε συσκευή έχει ακριβώς τα δικαιώματα που χρειάζεται. Οι αισθητήρες μπορούν μόνο να στέλνουν μετρήσεις — δεν μπορούν να δώσουν εντολή σε τίποτα.', {
    x:M+0.45, y:4.82, w:4.6, h:1.0, fontSize:13, color:PALE, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:18 });
  const rows = [
    ['FaLock', 'Κρυπτογράφηση σε κάθε σύνδεση', 'Η εφαρμογή αναγνωρίζει τη δική σου μονάδα και μόνο αυτήν.'],
    ['FaQrcode', 'Ζευγάρωμα με QR ή εξαψήφιο PIN', 'Τα κλειδιά περνούν εκτός δικτύου — δεν ταξιδεύουν ποτέ στον αέρα.'],
    ['FaEye', 'Καταγραφή και ειδοποίηση συμβάντων', 'Με όριο συχνότητας, ώστε οι ίδιες οι ειδοποιήσεις να μη γίνουν όπλο.'],
    ['FaFlaskVial', 'Μοναδικά μυστικά ανά μονάδα', 'Καμία κοινή προεπιλογή, και μία εντολή τα αλλάζει όλα.'],
  ];
  rows.forEach(([i, h, t], k) => {
    const y = 2.3 + k * 0.94;
    badge(s, 6.6, y, i, 0.56);
    s.addText(h, { x:7.35, y:y+0.0, w:5.28, h:0.3, fontSize:13.5, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:7.35, y:y+0.33, w:5.28, h:0.5, fontSize:12, lineSpacing:16 });
  });
  s.addText('Πέντε πραγματικές ευπάθειες εντοπίστηκαν και έκλεισαν σε ένα ειδικό πέρασμα ασφάλειας — και είναι όλες τεκμηριωμένες, όχι κρυμμένες.', {
    x:M, y:6.18, w:CW, h:0.44, fontSize:14.5, italic:true, color:INK, fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~50 δευτ. Για την επιτροπή: η ασφάλεια δεν προστέθηκε στο τέλος, έγινε ως ξεχωριστό πέρασμα με καταγραφή. Για τον πελάτη: το πότισμα δεν ανοίγει από ξένο.');
}

/* ============================ 11 · ΣΥΓΚΡΙΣΗ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'Η ΔΙΑΦΟΡΑ');
  title(s, 'Τι το κάνει διαφορετικό');
  const hdr = (t, opts) => Object.assign({ text:t, options: Object.assign({ bold:true, color:WHT,
    fill:{ color:INK }, fontFace:BF, fontSize:12.5, align:'center', valign:'middle' }, opts || {}) });
  const rows = [[
    { text:'', options:{ fill:{ color:INK } } },
    hdr('GreenHouse', { fill:{ color:LEAF } }),
    hdr('Εμπορικό σύστημα cloud'),
    hdr('Τυπική αυτοσχέδια λύση'),
  ]];
  const data = [
    ['Συνδρομή', 'Καμία', 'Μηνιαία, συχνά ανά αισθητήρα', 'Καμία'],
    ['Πού ζουν τα δεδομένα', 'Στο κτήμα σου', 'Σε ξένο server', 'Τοπικά, αν το στήσεις σωστά'],
    ['Χωρίς Internet', 'Λειτουργεί κανονικά', 'Σταματά', 'Συνήθως λειτουργεί'],
    ['Τροφοδοσία κόμβου', 'Μπαταρία και ήλιος, για μήνες', 'Ρεύμα ή ιδιόκτητο δίκτυο', 'Πρίζα σε κάθε σημείο'],
    ['Υποδομή στον χώρο', 'Καμία — το δίκτυο στήνεται μόνο του', 'Πύλη ανά περιοχή', 'Router και καλωδιώσεις'],
    ['Προσθήκη αισθητήρα', 'Σκανάρεις ένα QR', 'Παραγγελία και ενεργοποίηση', 'Επαναπρογραμματισμός'],
    ['Κόστος υλικού ανά κόμβο', 'Λίγα ευρώ', 'Δεκάδες έως εκατοντάδες', 'Λίγα ευρώ'],
  ];
  data.forEach((r, i) => {
    const bg = i % 2 ? TINT : WHT;
    rows.push([
      { text:r[0], options:{ bold:true, color:INK, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle' } },
      { text:r[1], options:{ bold:true, color:LEAF, fill:{ color:'EAF2E6' }, fontFace:BF, fontSize:12, valign:'middle', align:'center' } },
      { text:r[2], options:{ color:MUT, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle', align:'center' } },
      { text:r[3], options:{ color:MUT, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle', align:'center' } },
    ]);
  });
  s.addTable(rows, { x:M, y:2.28, w:CW, colW:[3.05, 3.05, 2.92, 2.91], rowH:0.46,
    border:{ type:'solid', color:'DFE8DB', pt:1 }, autoPage:false, margin:0.08 });
  footnote(s, 'Οι εμπορικές τιμές και δυνατότητες διαφέρουν ανά προμηθευτή. Η σύγκριση αφορά το μοντέλο λειτουργίας, όχι κάποιο συγκεκριμένο προϊόν.', 6.55);
  s.addNotes('~45 δευτ. Μην διαβάσεις όλο τον πίνακα. Δείξε δύο γραμμές: «χωρίς Internet» και «προσθήκη αισθητήρα». Αυτές είναι που κερδίζουν.');
}

/* ============================ 12 · ΤΡΕΙΣ ΚΛΙΜΑΚΕΣ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΠΡΟΟΠΤΙΚΗ');
  title(s, 'Το ίδιο δίκτυο, τρεις κλίμακες');
  const cs = [
    ['FaHouse', 'ΘΕΡΜΟΚΗΠΙΟ', 'Ζώνες μέσα σε έναν χώρο. Ένας εγκέφαλος, λίγοι κόμβοι, διαφορετικό κλίμα ανά ζώνη και πότισμα που αποφασίζεται μόνο του.', 'Εδώ ξεκινήσαμε'],
    ['FaSeedling', 'ΦΥΤΩΡΙΟ', 'Δεκάδες πάγκοι, άλλη παρτίδα ο καθένας. Προφίλ φυτού ανά πάγκο, ειδοποίηση ανά παρτίδα, ιστορικό ανά κύκλο παραγωγής.', 'Το επόμενο προϊόν'],
    ['FaTractor', 'ΧΩΡΑΦΙ', 'Χιλιόμετρα αντί για μέτρα. Ίδια λογική δικτύου, ραδιοεπικοινωνία μεγάλης εμβέλειας, κόμβοι που ζουν αποκλειστικά από τον ήλιο.', 'Ο ορίζοντας'],
  ];
  cs.forEach(([i, h, t, tag], k) => {
    const x = M + k * 4.08;
    card(s, x, 2.3, 3.78, 3.75);
    badge(s, x + 0.4, 2.62, i, 0.66);
    s.addText(h, { x:x+0.4, y:3.46, w:3.0, h:0.34, fontSize:17, bold:true, color:INK,
      fontFace:HF, charSpacing:1.2, isTextBox:true, margin:0 });
    body(s, t, { x:x+0.4, y:3.9, w:3.02, h:1.5, fontSize:12.5, lineSpacing:17.5 });
    s.addShape(p.ShapeType.roundRect, { x:x+0.4, y:5.36, w:1.85, h:0.36, fill:{ color:INK },
      line:{ color:INK, width:0 }, rectRadius:0.08 });
    s.addText(tag, { x:x+0.4, y:5.36, w:1.85, h:0.36, fontSize:10.5, bold:true, color:SUN,
      fontFace:BF, align:'center', valign:'middle', isTextBox:true, margin:0 });
  });
  s.addText('Ο κώδικας δεν αλλάζει όταν αλλάζει η κλίμακα. Αλλάζει μόνο πόσοι κόμβοι μιλούν.', {
    x:M, y:6.32, w:CW, h:0.44, fontSize:15, italic:true, color:INK, fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~45 δευτ. Πέρασε γρήγορα από θερμοκήπιο, μείνε στο φυτώριο (επόμενη διαφάνεια), κλείσε με το χωράφι ως ορίζοντα.');
}

/* ============================ 13 · ΦΥΤΩΡΙΟ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'Η ΑΓΟΡΑ ΠΟΥ ΠΟΝΑΕΙ ΠΕΡΙΣΣΟΤΕΡΟ');
  title(s, 'Το φυτώριο είναι το πραγματικό πρώτο προϊόν');
  const pain = [
    ['FaClock', 'Εκατοντάδες παρτίδες, ένας άνθρωπος', 'Κάθε πάγκος θέλει άλλη υγρασία και άλλη θερμοκρασία. Κανείς δεν προλαβαίνει να τους ελέγξει όλους, κάθε μέρα.'],
    ['FaTemperatureHalf', 'Ο διπλανός πάγκος έχει άλλο μικροκλίμα', 'Ένας αισθητήρας στη μέση του χώρου δεν λέει τίποτα για τη γωνία που παγώνει ή για τον πάγκο κάτω από τον εξαεριστήρα.'],
    ['FaTriangleExclamation', 'Η απώλεια σπορόφυτων είναι ακριβή', 'Δεν χάνεις μια μέτρηση — χάνεις τη δουλειά ενός ολόκληρου κύκλου παραγωγής και την παράδοση που είχες υποσχεθεί.'],
  ];
  pain.forEach(([i, h, t], k) => {
    const y = 2.3 + k * 1.38;
    badge(s, M, y, i, 0.58);
    s.addText(h, { x:M+0.78, y:y-0.02, w:5.35, h:0.32, fontSize:14.5, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:M+0.78, y:y+0.34, w:5.35, h:0.9, fontSize:12.5, lineSpacing:17 });
  });
  s.addShape(p.ShapeType.roundRect, { x:7.1, y:2.3, w:5.53, h:3.55, fill:{ color:INK },
    line:{ color:INK, width:0 }, rectRadius:0.1, shadow: shadow() });
  badge(s, 7.55, 2.72, 'FaLeaf', 0.68, SUN, 'd');
  s.addText('Το σύστημα έχει ήδη το κρίσιμο κομμάτι.', {
    x:7.55, y:3.6, w:4.65, h:0.72, fontSize:19, bold:true, color:WHT, fontFace:HF,
    isTextBox:true, margin:0, lineSpacing:26 });
  s.addText('Τα προφίλ φυτού ανά ζώνη υπάρχουν σήμερα μέσα στην εφαρμογή. Ένα φυτώριο δεν χρειάζεται καινούργια ιδέα — χρειάζεται τους ίδιους κόμβους, απλώς περισσότερους. Ίδιο υλικό, ίδια εφαρμογή, δέκα φορές περισσότεροι πάγκοι.', {
    x:7.55, y:4.42, w:4.65, h:1.3, fontSize:13, color:PALE, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:18.5 });
  s.addText('Ένα φυτώριο δεν αγοράζει αισθητήρες. Αγοράζει τη σιγουριά ότι δεν θα χαθεί μια παρτίδα.', {
    x:M, y:6.32, w:CW, h:0.44, fontSize:15, italic:true, color:INK, fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~50 δευτ. Εδώ είναι το εμπορικό επιχείρημα. Το φυτώριο έχει μετρήσιμη απώλεια ανά παρτίδα — άρα ξέρει να υπολογίσει την αξία του συστήματος χωρίς να του την εξηγήσεις.');
}

/* ============================ 14 · PLUG & PLAY ============================ */
{
  const s = p.addSlide();
  kicker(s, 'Η ΕΜΠΕΙΡΙΑ ΤΟΥ ΧΡΗΣΤΗ');
  title(s, 'Plug & play: το σκανάρεις, μπαίνει, δουλεύει.');
  const steps = [
    ['01', 'Βγάζεις τον αισθητήρα από το κουτί', 'Έρχεται με το δικό του κλειδί, τυπωμένο ως QR επάνω του. Καμία ρύθμιση, κανένα καλώδιο, κανένας κωδικός.'],
    ['02', 'Τον σκανάρεις με το κινητό', 'Ο εγκέφαλος τον δέχεται για λίγα λεπτά και μόνο τότε. Δεν χρειάζεται να αγγίξεις καμία άλλη συσκευή του δικτύου.'],
    ['03', 'Μετράει', 'Βρίσκει μόνος του δρόμο προς τη γέφυρα, εμφανίζεται στην εφαρμογή και αρχίζει να στέλνει. Χρόνος: δευτερόλεπτα.'],
  ];
  steps.forEach(([n, h, t], k) => {
    const x = M + k * 4.08;
    card(s, x, 2.28, 3.78, 2.35);
    s.addText(n, { x:x+0.4, y:2.5, w:1.0, h:0.5, fontSize:26, bold:true, color:MOSS,
      fontFace:HF, isTextBox:true, margin:0 });
    s.addText(h, { x:x+0.4, y:3.02, w:3.0, h:0.62, fontSize:14.5, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0, lineSpacing:19 });
    body(s, t, { x:x+0.4, y:3.68, w:3.02, h:0.85, fontSize:12, lineSpacing:16.5 });
  });
  bandDark(s, M, 4.85, CW, 1.35,
    'Κάθε κόμβος ξέρει μόνο τον εγκέφαλο. Οι ενδιάμεσοι κουβαλούν το πακέτο χωρίς να το διαβάζουν — γι’ αυτό η προσθήκη ενός αισθητήρα δεν αγγίζει κανέναν άλλον, και το πλήθος των αισθητήρων δεν έχει πρακτικό όριο.', 15);
  footnote(s, 'Το ίδιο μοτίβο κλειδιών που χρησιμοποιεί το Bluetooth Mesh: ένα κλειδί δικτύου για τη δρομολόγηση, ένα ξεχωριστό κλειδί ανά συσκευή για το περιεχόμενο.', 6.45);
  s.addNotes('~50 δευτ. Αυτή είναι η υπόσχεση του προϊόντος. Τόνισε ότι η αρχιτεκτονική είναι φτιαγμένη ώστε ο αριθμός των αισθητήρων να μην είναι πρόβλημα: ο ενδιάμεσος δεν διαβάζει, άρα δεν χρειάζεται να ξέρει κανέναν.');
}

/* ============================ 15 · ΤΕΛΙΚΟΣ ΣΤΟΧΟΣ ============================ */
{
  const s = p.addSlide();
  s.background = { color: INK };
  s.addText('Ο ΤΕΛΙΚΟΣ ΣΤΟΧΟΣ', { x:M, y:0.42, w:CW, h:0.3, fontSize:11.5, bold:true,
    color:SUN, fontFace:BF, charSpacing:2.2, isTextBox:true, margin:0 });
  s.addText('Το θερμοκήπιο κλείνει τον βρόχο μόνο του', { x:M, y:0.78, w:CW, h:0.7,
    fontSize:33, bold:true, color:WHT, fontFace:HF, isTextBox:true, margin:0 });
  const loop = [
    ['FaTemperatureHalf', 'ΑΙΣΘΑΝΕΤΑΙ', 'κάθε ζώνη ξεχωριστά'],
    ['FaCloudArrowUp', 'ΠΡΟΒΛΕΠΕΙ', 'με τον καιρό που έρχεται'],
    ['FaBrain', 'ΑΠΟΦΑΣΙΖΕΙ', 'με βάση το τι φυτεύτηκε'],
    ['FaBolt', 'ΔΡΑ', 'πότισμα, αερισμός, κάλυψη'],
    ['FaChartLine', 'ΜΑΘΑΙΝΕΙ', 'από το δικό του ιστορικό'],
  ];
  loop.forEach(([i, lab, sub], k) => {
    const cx = 1.98 + k * 2.34, d = 1.32;
    s.addShape(p.ShapeType.ellipse, { x:cx-d/2, y:2.3, w:d, h:d, fill:{ color:INK2 },
      line:{ color:'2E5B48', width:1.25 } });
    s.addImage({ path: ic(i, 's'), x:cx-0.24, y:2.72, w:0.48, h:0.48 });
    s.addText(lab, { x:cx-1.05, y:3.78, w:2.1, h:0.3, fontSize:12.5, bold:true, color:WHT,
      fontFace:BF, align:'center', charSpacing:0.8, isTextBox:true, margin:0 });
    s.addText(sub, { x:cx-1.1, y:4.08, w:2.2, h:0.5, fontSize:11, color:DIM, fontFace:BF,
      align:'center', isTextBox:true, margin:0, lineSpacing:14.5 });
    if (k < 4) s.addImage({ path: ic('FaArrowRight','s'), x:cx+0.92, y:2.82, w:0.28, h:0.28 });
  });
  s.addText('Κάθε μονάδα κρατά δύο χρόνια ιστορικού ανά ζώνη. Με τον καιρό δεν ξέρει μόνο τι συμβαίνει — ξέρει τι δουλεύει σε αυτό ακριβώς το θερμοκήπιο, με αυτό ακριβώς το χώμα.', {
    x:1.4, y:4.95, w:10.5, h:0.75, fontSize:14.5, color:PALE, fontFace:BF, align:'center',
    isTextBox:true, margin:0, lineSpacing:21 });
  s.addText('Ο στόχος δεν είναι να σου δείχνει νούμερα. Είναι να μη χρειάζεται να τα κοιτάξεις.', {
    x:1.4, y:5.95, w:10.5, h:0.6, fontSize:20, bold:true, color:SUN, fontFace:HF,
    align:'center', isTextBox:true, margin:0 });
  s.addNotes('~55 δευτ. Ο κύκλος είναι το θεωρητικό επιχείρημα: παρακολούθηση → πρόβλεψη → δράση → μάθηση. Το τελευταίο βήμα (μαθαίνει) είναι αυτό που κάνει κάθε εγκατάσταση να αξίζει περισσότερο με τον χρόνο.');
}

/* ============================ 16 · ΑΞΙΑ ΠΕΡΑ ΑΠΟ ΤΑ ΦΥΤΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'Η ΓΕΝΙΚΗ ΑΞΙΑ');
  title(s, 'Το θερμοκήπιο είναι η πρώτη εφαρμογή. Το δίκτυο είναι το προϊόν.', INK, 30, 0.9);
  body(s, 'Το πρόβλημα που λύθηκε είναι γενικό: αξιόπιστες μετρήσεις από συσκευές με μπαταρία, σε χώρους χωρίς υποδομή, χωρίς εξάρτηση από cloud, με ασφάλεια εξαρχής. Άλλαξε τον αισθητήρα επάνω — το υπόλοιπο μένει ίδιο.', {
    x:M, y:1.85, w:11.2, h:0.85, fontSize:14.5, color:MUT, lineSpacing:21 });
  const uses = [
    ['FaTruckFast', 'Ψυκτική αλυσίδα', 'Θερμοκρασία σε κάθε παλέτα, όχι μόνο σε κάθε φορτηγό.'],
    ['FaFish', 'Υδατοκαλλιέργεια', 'Δεξαμενές που δεν έχουν ούτε ρεύμα ούτε δίκτυο.'],
    ['FaBuilding', 'Κτίρια και μουσεία', 'Υγρασία ανά αίθουσα, χωρίς νέα καλωδίωση σε διατηρητέο.'],
    ['FaSolarPanel', 'Ενεργειακά πάρκα', 'Παρακολούθηση σε εκτάσεις όπου δεν υπάρχει τίποτα.'],
    ['FaFlaskVial', 'Περιβαλλοντική έρευνα', 'Μακροχρόνιες μετρήσεις σε σημεία χωρίς πρίζα.'],
  ];
  uses.forEach(([i, h, t], k) => {
    const x = M + k * 2.43;
    card(s, x, 2.95, 2.2, 2.5);
    badge(s, x + 0.26, 3.22, i, 0.56);
    s.addText(h, { x:x+0.26, y:3.92, w:1.82, h:0.55, fontSize:12, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0, lineSpacing:16 });
    body(s, t, { x:x+0.26, y:4.5, w:1.84, h:0.88, fontSize:10.5, lineSpacing:14.5 });
  });
  bandDark(s, M, 5.72, CW, 1.05,
    'Ίδιοι κόμβοι, ίδιο πρωτόκολλο, άλλος αισθητήρας επάνω. Η δύσκολη δουλειά — ενέργεια, δρομολόγηση, ασφάλεια — έχει ήδη γίνει μία φορά.', 15);
  s.addNotes('~45 δευτ. Αυτή είναι η διαφάνεια για την επιτροπή και για επενδυτή ταυτόχρονα: το έργο δεν είναι μια εφαρμογή γεωργίας, είναι μια πλατφόρμα τηλεμετρίας που έτυχε να ξεκινήσει από θερμοκήπιο.');
}

/* ============================ 17 · ΚΛΕΙΣΙΜΟ ============================ */
{
  const s = p.addSlide();
  s.background = { color: INK };
  motif(s, 9.15, 4.35, 0.78, '224E3E');
  s.addText('Ένα θερμοκήπιο που το κοιτάς\nόταν θέλεις — όχι όταν πρέπει.', {
    x:0.9, y:1.72, w:10.3, h:1.7, fontSize:31, bold:true, color:WHT, fontFace:HF,
    isTextBox:true, margin:0, lineSpacing:44 });
  const next = ['Ολοκλήρωση της εμπειρίας plug & play',
                'Πιλοτική εγκατάσταση σε φυτώριο',
                'Κόμβοι μεγάλης εμβέλειας για ανοιχτό χωράφι'];
  next.forEach((t, k) => {
    const y = 4.15 + k * 0.62;
    s.addImage({ path: ic('FaCheck','s'), x:0.95, y:y+0.06, w:0.24, h:0.24 });
    s.addText(t, { x:1.42, y:y, w:8.0, h:0.38, fontSize:15, color:PALE, fontFace:BF,
      isTextBox:true, margin:0 });
  });
  s.addText('GreenHouse  ·  Σεπτέμβριος 2026', { x:0.9, y:6.55, w:6, h:0.3, fontSize:11,
    color:'6E8A78', fontFace:BF, isTextBox:true, margin:0 });
  s.addNotes('~30 δευτ. Κλείσε με την ατάκα, μετά τα τρία επόμενα βήματα, και άνοιξε για ερωτήσεις. Τα παραρτήματα που ακολουθούν είναι για τις ερωτήσεις.');
}

/* ============================ Π1 · ΤΙ ΤΡΕΧΕΙ ΣΗΜΕΡΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΠΑΡΑΡΤΗΜΑ');
  title(s, 'Πού βρίσκεται το έργο σήμερα');
  const tiles = [['22.000+', 'γραμμές κώδικα'], ['6', 'υπηρεσίες στον εγκέφαλο'],
                 ['45/45', 'έλεγχοι συστήματος, όλοι επιτυχείς'], ['65', 'αρχεία αυτόματων δοκιμών']];
  tiles.forEach(([b, l], k) => {
    const x = M + k * 3.05;
    card(s, x, 2.3, 2.7, 1.42);
    s.addText(b, { x:x+0.3, y:2.42, w:2.1, h:0.48, fontSize:23, bold:true, color:LEAF,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, l, { x:x+0.3, y:2.92, w:2.15, h:0.62, fontSize:11.5, lineSpacing:15 });
  });
  const done = [
    'Γέφυρα και κόμβοι αισθητήρων λειτουργούν σε πραγματικό υλικό· η αναμετάδοση μέσω ενδιάμεσου κόμβου έχει επαληθευτεί στον αέρα (Αύγουστος 2026).',
    'Εφαρμογή Android: πίνακας ανά ζώνη, ζωντανός χάρτης δικτύου, ιστορικό με εξαγωγή CSV, κανόνες αυτοματισμού, ειδοποιήσεις, προφίλ φυτών.',
    'Απομακρυσμένη πρόσβαση εκτός τοπικού δικτύου, με κρυπτογράφηση και ζευγάρωμα μέσω QR.',
    'Εγκατάσταση από μηδέν: το σύστημα σηκώνει δικό του δίκτυο για την πρώτη ρύθμιση — δεν χρειάζεται οθόνη ή πληκτρολόγιο.',
    'Τεκμηρίωση: αρχιτεκτονική, τεχνικό εγχειρίδιο ανά επίπεδο δικτύου, ανάλυση ασφάλειας, ενεργειακή μελέτη.',
  ];
  s.addText(done.map((t, i) => ({ text:t, options:{ bullet:true, breakLine: i < done.length - 1 } })), {
    x:M, y:4.02, w:11.6, h:2.6, fontSize:13, color:MUT, fontFace:BF, isTextBox:true,
    margin:0, lineSpacing:18, paraSpaceAfter:10 });
  s.addNotes('Παράρτημα. Χρησιμοποίησέ το μόνο αν ρωτήσουν «τι δουλεύει πραγματικά τώρα».');
}

/* ============================ Π2 · ΕΝΕΡΓΕΙΑ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΠΑΡΑΡΤΗΜΑ');
  title(s, 'Ενεργειακός προϋπολογισμός ενός κόμβου');
  const rows = [[
    { text:'Μέγεθος', options:{ bold:true, color:WHT, fill:{ color:INK }, fontFace:BF, fontSize:12.5, valign:'middle' } },
    { text:'Τιμή', options:{ bold:true, color:WHT, fill:{ color:INK }, fontFace:BF, fontSize:12.5, valign:'middle' } },
    { text:'Τι σημαίνει', options:{ bold:true, color:WHT, fill:{ color:INK }, fontFace:BF, fontSize:12.5, valign:'middle' } },
  ]];
  const data = [
    ['Κύκλος μέτρησης', '15 λεπτά', 'Αρκετά πυκνά για κλίμα θερμοκηπίου, αρκετά αραιά για μπαταρία.'],
    ['Χρόνος ξύπνιου', '~2,5 δευτερόλεπτα', 'Το 0,28% του χρόνου. Τον υπόλοιπο, το ραδιόφωνο είναι σβηστό.'],
    ['Ρεύμα ενεργά', '~86,5 mA', 'Μόνο όσο διαρκεί η μέτρηση και η αποστολή.'],
    ['Ρεύμα στον ύπνο', '~62,5 µA', 'Χίλιες τετρακόσιες φορές λιγότερο από την ενεργή κατάσταση.'],
    ['Κατανάλωση', '~7,3 mAh την ημέρα', 'Λιγότερο απ’ όσο χάνει μόνη της μια μπαταρία στο ράφι.'],
    ['Μπαταρία', '1500 mAh', 'Μία κοινή επαναφορτιζόμενη κυψέλη.'],
    ['Αυτονομία χωρίς ήλιο', '~205 ημέρες', 'Σχεδόν επτά μήνες σε απόλυτο σκοτάδι.'],
    ['Ηλιακό πάνελ', '2 W', 'Περίπου 60 φορές περισσότερη ενέργεια απ’ όση χρειάζεται, στον χειμώνα.'],
  ];
  data.forEach((r, i) => {
    const bg = i % 2 ? TINT : WHT;
    rows.push([
      { text:r[0], options:{ bold:true, color:INK, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle' } },
      { text:r[1], options:{ bold:true, color:LEAF, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle' } },
      { text:r[2], options:{ color:MUT, fill:{ color:bg }, fontFace:BF, fontSize:12, valign:'middle' } },
    ]);
  });
  s.addTable(rows, { x:M, y:2.3, w:CW, colW:[3.1, 2.6, 6.23], rowH:0.44,
    border:{ type:'solid', color:'DFE8DB', pt:1 }, autoPage:false, margin:0.08 });
  footnote(s, 'Οι τιμές προκύπτουν από τις σταθερές λειτουργίας του κόμβου· η επιβεβαίωση σε συνθήκες πεδίου βρίσκεται σε εξέλιξη.', 6.4);
  s.addNotes('Παράρτημα. Για την ερώτηση «πόσο κρατάει η μπαταρία και πώς το ξέρετε».');
}

/* ============================ Π3 · ΠΗΓΕΣ ============================ */
{
  const s = p.addSlide();
  kicker(s, 'ΠΑΡΑΡΤΗΜΑ');
  title(s, 'Πηγές και τεκμηρίωση');
  const src = [
    ['FaSeedling', 'Θερμοκηπιακές εκτάσεις στην Ελλάδα', 'Ελληνική Στατιστική Αρχή (ΕΛΣΤΑΤ), ετήσια γεωργική έρευνα — στοιχεία 2018: 59.983 στρέμματα κηπευτικών και 6.017 στρέμματα ανθοκομικών υπό κάλυψη.'],
    ['FaDroplet', 'Άρδευση και κατανάλωση νερού', 'Latinopoulos, «Valuation and pricing of irrigation water», Global NEST Journal — η άρδευση αντιστοιχεί σε 80–85% της συνολικής κατανάλωσης νερού στην Ελλάδα.'],
    ['FaCloudArrowUp', 'Πρόγνωση καιρού', 'Open-Meteo — ανοιχτή υπηρεσία πρόγνωσης, χωρίς κλειδί και χωρίς κόστος, που τροφοδοτεί τους κανόνες και τις ειδοποιήσεις παγετού.'],
    ['FaChartLine', 'Τεχνική τεκμηρίωση του έργου', 'Αρχιτεκτονική, τεχνικό εγχειρίδιο ανά επίπεδο δικτύου, ανάλυση ασφάλειας και ενεργειακή μελέτη — όλα μέσα στο αποθετήριο του έργου.'],
  ];
  src.forEach(([i, h, t], k) => {
    const y = 2.3 + k * 1.15;
    badge(s, M, y, i, 0.56);
    s.addText(h, { x:M+0.78, y:y-0.02, w:11.0, h:0.3, fontSize:14, bold:true, color:INK,
      fontFace:HF, isTextBox:true, margin:0 });
    body(s, t, { x:M+0.78, y:y+0.32, w:11.0, h:0.66, fontSize:12, lineSpacing:16.5 });
  });
  s.addNotes('Παράρτημα. Οι δύο στατιστικές πηγές είναι αυτές που στηρίζουν τα νούμερα της δεύτερης διαφάνειας.');
}

/* ---------------------------------------------------------------------------
 * Repair a pptxgenjs bug before the file is handed over.
 *
 * For every 2D bar/column chart the library appends THREE <c:axId> children to
 * <c:barChart> (its AXIS_ID_SERIES_PRIMARY among them) but only ever emits the
 * matching <c:serAx> element for BAR3D charts. The result references an axis
 * that does not exist, and CT_BarChart accepts exactly two axis ids anyway.
 *
 * PowerPoint refuses to open the whole presentation. LibreOffice ignores the
 * stray id and renders fine, so a render-based QA pass never catches it.
 *
 * This strips any <c:axId> inside a chart group that no axis element defines.
 * ------------------------------------------------------------------------- */
async function dropDanglingAxisIds(file) {
  const fs = require('fs');
  const JSZip = require('jszip');
  const zip = await JSZip.loadAsync(fs.readFileSync(file));
  const AXIS = /<c:(?:catAx|valAx|serAx|dateAx)>([\s\S]*?)<\/c:(?:catAx|valAx|serAx|dateAx)>/g;
  let removed = 0;

  for (const path of Object.keys(zip.files)) {
    if (!/^ppt\/charts\/chart\d+\.xml$/.test(path)) continue;
    const xml = await zip.file(path).async('string');

    const defined = new Set();
    for (const m of xml.matchAll(AXIS)) {
      const id = m[1].match(/<c:axId val="(\d+)"\s*\/>/);
      if (id) defined.add(id[1]);
    }

    const fixed = xml.replace(/<c:(\w+Chart)>([\s\S]*?)<\/c:\1>/g, (whole, tag, inner) =>
      `<c:${tag}>` + inner.replace(/<c:axId val="(\d+)"\s*\/>/g, (node, id) => {
        if (defined.has(id)) return node;
        removed++;
        return '';
      }) + `</c:${tag}>`);

    if (fixed !== xml) zip.file(path, fixed);
  }

  if (removed) {
    fs.writeFileSync(file, await zip.generateAsync({
      type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 6 },
    }));
  }
  return removed;
}

p.writeFile({ fileName: 'GreenHouse_Parousiasi.pptx' })
  .then(async (f) => {
    const removed = await dropDanglingAxisIds(f);
    console.log('wrote', f, removed ? `(removed ${removed} dangling axis id(s))` : '(no chart repair needed)');
  })
  .catch((e) => { console.error(e); process.exit(1); });
