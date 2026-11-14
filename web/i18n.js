/* Vanilla Sphere - the two languages of the interface.
   Fixed text in index.html is English and carries data-i18n="key"; the Vietnamese for that key
   lives in STR.vi. Text put on the page by script exists in both tables and is read with tr(). */
"use strict";

const STR = {
  en: {
    "go": "Show sky", "go.busy": "Searching…",
    "q.adv": "RA Dec or an object name", "q.simple": "Search a star or galaxy by name",
    "load.search": "Searching the SPHEREx archive…",
    "load.read": "Reading exposure {done} of {total} from NASA's archive…",
    "load.done": "{n} exposures in {s}s",
    "load.frames": "Loading frames…",
    "load.fail": "That did not work: {msg}",
    "load.none": "SPHEREx has no usable images at this position (yet) for the chosen detectors.",
    "load.none.simple": "SPHEREx has no photos of this spot yet. Go back to the sky map and try another one.",
    "err.name": "No object called “{name}” was found. Check the spelling, or enter coordinates such as 269.4486 +4.7387.",
    "err.name.simple": "Nothing called “{name}” was found. Check the spelling, or pick a spot on the sky map.",
    "field.cell": "{n} cell", "field.cells": "{n} cells",
    "field.done": "{cells} · {n} exposures in {s}s", "field.shown": "{cells} shown · ",
    "field.read": "reading exposure {done} of {total} for {cells}…", "field.search": "searching the archive for {cells}…",
    "cat.loading": "Loading {cat}…", "cat.failed": "{cat} did not answer. Untick and tick to try again.",
    "track.pos": "{n} position", "track.poss": "{n} positions",
    "hud.sum": "sum of {n} frames", "hud.median": " − median", "hud.exp": "{n} exposure", "hud.exps": "{n} exposures",
    "ep.date": "Date (UTC)", "ep.det": "Detectors", "ep.col": "Colours", "ep.exp": "Exposures", "ep.cov": "Coverage",
    "ep.covv": "{p}% of field", "ep.flags": "Flags at target", "ep.clean": "clean", "ep.from": " (from {date})", "ep.nodata": "no data",
    "hint.recentre": "Double-click the image to recentre", "hint.cell": "Double-click a cell to open it at full detail",
    "plot.spec": "Spectrum at the crosshair", "plot.lc": "Light curve at the crosshair",
    "plot.none": "No clean flux here: the pixels are saturated, masked or off the detector.",
    "plot.x.lc": "Date (UTC)", "plot.x.spec": "Wavelength (µm)", "plot.y": "Flux density (mJy)",
    "plot.pass": "pass {m}", "plot.flagged": "flagged pixels",
    "plot.note.lc": "{n} measurements, coloured by detector. Each exposure samples a different wavelength, so compare points of one colour.",
    "plot.note.spec": "{n} points built on the fly: each exposure sees the target through a different slice of the variable filter. Colours are sky passes; a change between passes means variability. Hollow points have flagged pixels in the aperture.",
    "play": "Play", "pause": "Pause", "copy": "Copy", "copied": "Copied",
    "det.rgb.t": "False colour: red = D6 (or D5), green = D4 (or D3), blue = D2 (or D1). A channel not observed on a date is borrowed from the nearest date and marked.",
    "det.all": "All bands", "det.all.t": "All six detectors averaged, 0.75-5.0 µm",
    "target.pm": "Centred on its predicted position for late 2025 (it moves {v}″/yr).",
    "simple.photo": "Photo {i} of {n}",
    "simple.spot": "Sky in {con}", "simple.spot.none": "A spot in the sky",
    "simple.story.spot": "You picked a spot in the constellation {con}. Press play: distant stars stay still. Anything that moves or flickers is worth a closer look.",
    "simple.story.named": "{name} lies in the constellation {con}. Press play: distant stars stay still. Anything that moves or flickers is worth a closer look.",
    "simple.shared": "Link copied",
    "story.barnard": "A small red star only 6 light-years away. No other star crosses our sky as fast. Press play and watch it drift past the distant stars behind it.",
    "story.eps-indi-b": "Two brown dwarfs, bodies too small to shine like a star, circling the nearby star Epsilon Indi 12 light-years away. Look closely at the centre: they shift by about one pixel a year.",
    "story.wise-0855": "The coldest brown dwarf known: colder than ice, and just 7 light-years away. It is extremely faint, so it may be hard to spot, yet it is one of the fastest movers in the sky.",
    "story.rw-aur": "A newborn star still gathering gas and dust. Now and then it fades for months, probably when dust passes in front of it. It stays in place: watch its brightness change.",
    "story.r-dra": "An old red giant that swells and shrinks like a slow heartbeat, growing bright and dim again about every 8 months. It stays in place: watch it pulse.",
    "kind.barnard": "Fastest-moving star", "kind.eps-indi-b": "Pair of brown dwarfs", "kind.wise-0855": "Coldest brown dwarf",
    "kind.rw-aur": "Young flickering star", "kind.r-dra": "Pulsing red giant",
    "map.aria": "Map of the whole sky. Drag to turn it, scroll to zoom, select a point to open it.",
    "map.open": "Opening…", "s.share": "Share",
  },
  vi: {
    "go": "Xem", "go.busy": "Đang tìm…",
    "q.adv": "RA Dec hoặc tên thiên thể", "q.simple": "Tìm một ngôi sao hay thiên hà theo tên",
    "load.search": "Đang tìm trong kho ảnh SPHEREx…",
    "load.read": "Đang đọc lần phơi sáng {done} / {total} từ kho của NASA…",
    "load.done": "{n} lần phơi sáng trong {s} giây",
    "load.frames": "Đang tải khung hình…",
    "load.fail": "Không thực hiện được: {msg}",
    "load.none": "SPHEREx chưa có ảnh dùng được ở vị trí này cho các đầu thu đã chọn.",
    "load.none.simple": "SPHEREx chưa có ảnh của điểm này. Hãy quay lại bản đồ sao và thử điểm khác.",
    "err.name": "Không tìm thấy thiên thể tên “{name}”. Hãy kiểm tra chính tả, hoặc nhập toạ độ như 269.4486 +4.7387.",
    "err.name.simple": "Không tìm thấy “{name}”. Hãy kiểm tra chính tả, hoặc chọn một điểm trên bản đồ sao.",
    "field.cell": "{n} ô", "field.cells": "{n} ô",
    "field.done": "{cells} · {n} lần phơi sáng trong {s} giây", "field.shown": "đang hiện {cells} · ",
    "field.read": "đang đọc lần phơi sáng {done} / {total} cho {cells}…", "field.search": "đang tìm trong kho ảnh cho {cells}…",
    "cat.loading": "Đang tải {cat}…", "cat.failed": "{cat} không phản hồi. Bỏ chọn rồi chọn lại để thử lần nữa.",
    "track.pos": "{n} vị trí", "track.poss": "{n} vị trí",
    "hud.sum": "tổng {n} khung hình", "hud.median": " − trung vị", "hud.exp": "{n} lần phơi sáng", "hud.exps": "{n} lần phơi sáng",
    "ep.date": "Ngày (UTC)", "ep.det": "Đầu thu", "ep.col": "Màu", "ep.exp": "Số lần phơi sáng", "ep.cov": "Độ phủ",
    "ep.covv": "{p}% vùng ảnh", "ep.flags": "Cờ tại mục tiêu", "ep.clean": "sạch", "ep.from": " (lấy từ {date})", "ep.nodata": "không có dữ liệu",
    "hint.recentre": "Nhấp đúp vào ảnh để đặt lại tâm", "hint.cell": "Nhấp đúp một ô để mở ở độ chi tiết đầy đủ",
    "plot.spec": "Phổ tại tâm ngắm", "plot.lc": "Đường cong sáng tại tâm ngắm",
    "plot.none": "Không có thông lượng sạch ở đây: điểm ảnh bị bão hoà, bị che hoặc nằm ngoài đầu thu.",
    "plot.x.lc": "Ngày (UTC)", "plot.x.spec": "Bước sóng (µm)", "plot.y": "Mật độ thông lượng (mJy)",
    "plot.pass": "lượt {m}", "plot.flagged": "điểm ảnh bị gắn cờ",
    "plot.note.lc": "{n} phép đo, tô màu theo đầu thu. Mỗi lần phơi sáng lấy một bước sóng khác nhau, nên hãy so các điểm cùng màu.",
    "plot.note.spec": "{n} điểm được dựng tức thời: mỗi lần phơi sáng nhìn mục tiêu qua một lát khác nhau của kính lọc biến thiên. Màu là các lượt quét bầu trời; khác nhau giữa các lượt nghĩa là có biến thiên. Điểm rỗng có điểm ảnh bị gắn cờ trong khẩu độ.",
    "play": "Phát", "pause": "Dừng", "copy": "Chép", "copied": "Đã chép",
    "det.rgb.t": "Màu giả: đỏ = D6 (hoặc D5), lục = D4 (hoặc D3), lam = D2 (hoặc D1). Kênh không được chụp trong ngày sẽ mượn từ ngày gần nhất và có đánh dấu.",
    "det.all": "Mọi dải", "det.all.t": "Trung bình cả sáu đầu thu, 0,75-5,0 µm",
    "target.pm": "Đặt tâm tại vị trí dự đoán cuối năm 2025 (nó dịch {v}″ mỗi năm).",
    "simple.photo": "Ảnh {i} / {n}",
    "simple.spot": "Bầu trời trong chòm {con}", "simple.spot.none": "Một điểm trên bầu trời",
    "simple.story.spot": "Bạn đã chọn một điểm trong chòm sao {con}. Bấm phát: các ngôi sao xa sẽ đứng yên. Thứ gì di chuyển hoặc nhấp nháy thì đáng để nhìn kĩ hơn.",
    "simple.story.named": "{name} nằm trong chòm sao {con}. Bấm phát: các ngôi sao xa sẽ đứng yên. Thứ gì di chuyển hoặc nhấp nháy thì đáng để nhìn kĩ hơn.",
    "simple.shared": "Đã chép liên kết",
    "story.barnard": "Một ngôi sao đỏ nhỏ chỉ cách chúng ta 6 năm ánh sáng. Không ngôi sao nào trôi trên bầu trời nhanh bằng nó. Bấm phát và xem nó trượt qua những ngôi sao xa phía sau.",
    "story.eps-indi-b": "Hai sao lùn nâu, những thiên thể quá nhỏ để tự cháy sáng như một ngôi sao, quay quanh sao Epsilon Indi cách ta 12 năm ánh sáng. Hãy nhìn kĩ vào tâm ảnh: mỗi năm chúng dịch khoảng một điểm ảnh.",
    "story.wise-0855": "Sao lùn nâu lạnh nhất từng biết: lạnh hơn cả nước đá, và chỉ cách ta 7 năm ánh sáng. Nó cực kì mờ nên có thể khó thấy, nhưng lại là một trong những thiên thể trôi nhanh nhất trên bầu trời.",
    "story.rw-aur": "Một ngôi sao mới sinh, vẫn đang hút khí và bụi quanh nó. Thỉnh thoảng nó mờ đi suốt nhiều tháng, có lẽ do bụi che ngang. Nó đứng yên: hãy xem độ sáng của nó thay đổi.",
    "story.r-dra": "Một sao khổng lồ đỏ già, phồng lên rồi co lại như nhịp tim chậm, cứ khoảng 8 tháng lại sáng lên rồi mờ đi. Nó đứng yên: hãy xem nó sáng tối theo nhịp.",
    "kind.barnard": "Ngôi sao trôi nhanh nhất", "kind.eps-indi-b": "Cặp sao lùn nâu", "kind.wise-0855": "Sao lùn nâu lạnh nhất",
    "kind.rw-aur": "Sao trẻ chập chờn", "kind.r-dra": "Sao khổng lồ đỏ co giãn",
    "map.aria": "Bản đồ cả bầu trời. Kéo để xoay, cuộn để phóng to, chọn một điểm để mở.",
    "map.open": "Đang mở…",

    /* fixed text of index.html */
    "h.map": "Bản đồ sao", "h.simple": "Đơn giản", "h.adv": "Chuyên sâu", "h.field": "Vùng",
    "h.api": "API", "h.source": "Mã nguồn",
    "h.credits": "<p><b>Ảnh</b> Ảnh phổ SPHEREx Level 2 của NASA (DR1, QR3), NASA/IPAC Infrared Science Archive, đọc trực tiếp từ bản sao AWS Open Data.</p><p><b>Lớp phủ</b> ESA Gaia DR3 và CatWISE2020, qua IRSA.</p><p><b>Bản đồ sao</b> Dữ liệu sao, chòm sao và dải Ngân Hà từ d3-celestial (Olaf Frohn, BSD-3).</p><p><b>Đội</b> Vanilla Sphere, NASA Space Apps Challenge 2026, “Planet X and SPHEREx”.</p>",
    "map.title": "Chọn một điểm trên bầu trời",
    "map.sub": "Kính SPHEREx của NASA chụp đi chụp lại cả bầu trời. Bấm vào bất cứ đâu để xem nơi đó thay đổi.",
    "map.start": "Bắt đầu từ đây", "map.hint": "Kéo để xoay · cuộn để phóng to · bấm để xem kĩ",
    "map.in": "Phóng to", "map.out": "Thu nhỏ", "map.reset": "Cả bầu trời",
    "r.targets": "Mục tiêu của đề bài", "r.display": "Hiển thị", "r.bands": "Dải phổ", "r.group": "Mỗi khung hình là",
    "r.visit": "Lượt quét", "r.day": "Ngày", "r.exposure": "Phơi sáng", "r.math": "Phép tính ảnh",
    "r.blink": "Nháy ảnh", "r.median": "− Trung vị", "r.ref": "− Ảnh mốc", "r.sum": "Cộng tất cả", "r.useref": "Dùng khung hình này",
    "r.contrast": "Tương phản", "r.reset": "Đặt lại", "r.overlays": "Lớp phủ", "r.cross": "Tâm ngắm", "r.smooth": "Làm mịn ảnh",
    "r.tiles": "Lưới ô", "r.tracks": "Quỹ đạo đã đo",
    "t.speed": "Tốc độ", "a.spectrum": "Phổ", "a.lc": "Đường cong sáng", "a.frame": "Khung hình này", "a.measured": "Đã đo trong vùng này",
    "a.pos": "Vị trí tâm ngắm", "a.dec": "Thập phân", "a.sexa": "Lục thập phân", "a.same": "Cùng vùng này trên",
    "a.share": "Liên kết hoặc nhúng", "a.page": "Trang", "a.gif": "GIF", "a.data": "Dữ liệu",
    "a.sharetip": "Toạ độ nằm ngay trong URL nên mọi góc nhìn đều có thể gửi hoặc nhúng. Cùng tham số đó trả về PNG, GIF hoặc số đo; xem API.",
    "a.try": "Gợi ý", "a.try1": "Phát các khung hình; thứ gì di chuyển sẽ nhảy.", "a.try2": "Xoá mọi thứ đứng yên.",
    "a.try3": "0,75–5,0 µm tại tâm ngắm.", "a.try4": "Gaia DR3, CatWISE, quỹ đạo của đội.", "a.try5": "Mỗi góc nhìn là một URL, một PNG hoặc một GIF.",
    "a.link": "Liên kết",
    "s.what": "<p>Đây là ảnh thật từ kính không gian SPHEREx của NASA, mỗi ảnh ứng với một ngày kính nhìn vào điểm này.</p><p>SPHEREx thấy ánh sáng hồng ngoại mà mắt ta không thấy; ba màu trong ảnh thay cho ba dải hồng ngoại. Vùng nâu sẫm là nơi chưa có dữ liệu.</p>",
    "s.share": "Chia sẻ", "s.gif": "Tải GIF", "s.toadv": "Xem số đo, dải phổ và phổ ở chế độ Chuyên sâu",
  },
};

let LANG = "en";
/* Text for script-made strings: tr("load.read", {done: 3, total: 9}). */
function tr(key, vars) {
  let s = STR[LANG][key];
  if (s === undefined) s = STR.en[key];
  if (s === undefined) return key;
  return vars ? s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] !== undefined ? vars[k] : "")) : s;
}
/* Swap the fixed text of the page. English is what index.html holds, kept aside on first use. */
function applyLang(lang) {
  LANG = lang === "vi" ? "vi" : "en";
  document.documentElement.lang = LANG;
  for (const el of document.querySelectorAll("[data-i18n]")) {
    if (el.dataset.en === undefined) el.dataset.en = el.innerHTML;
    const v = LANG === "vi" ? STR.vi[el.dataset.i18n] : undefined;
    el.innerHTML = v !== undefined ? v : el.dataset.en;
  }
  for (const attr of ["title", "aria-label", "placeholder"]) {
    for (const el of document.querySelectorAll(`[data-i18n-${attr}]`)) {
      const keep = "en" + attr.replace("-", "");
      if (el.dataset[keep] === undefined) el.dataset[keep] = el.getAttribute(attr) || "";
      const v = LANG === "vi" ? STR.vi[el.getAttribute(`data-i18n-${attr}`)] : undefined;
      el.setAttribute(attr, v !== undefined ? v : el.dataset[keep]);
    }
  }
}
