// Builds the Final Design Document (.docx) from the Markdown chapters in content/.
//
//   cd docs/fdd && npm install && node build.js
//
// Writes L13_MTLJ_Final_Design_Document.docx next to this file. Open it in Word and accept
// "update fields" so the table of contents gets its page numbers.
//
// The Markdown is a small subset:
//   # / ## / ###                headings (numbers are written in the text)
//   - item, 1. item             bullet and numbered lists (one level)
//   | a | b |                   pipe tables, preceded by a line "Table: caption {#id}"
//   ![caption](path){width=6 #id}  figures, numbered automatically
//   > NOTE: text                a highlighted note for the team to check before submitting
//   **bold**, *italic*, `code`  inline styles
//   {fig:id} {tab:id} {ref:key} cross-references, replaced with numbers
//   [[PAGEBREAK]] [[TOC]] [[LANDSCAPE]] [[PORTRAIT]]

const fs = require("fs");
const path = require("path");
const {
  AlignmentType, BorderStyle, Document, Footer, Header, HeadingLevel, ImageRun, LevelFormat,
  PageBreak, PageNumber, PageOrientation, Packer, Paragraph, ShadingType, Table, TableCell,
  TableOfContents, TableRow, TextRun, WidthType,
} = require("docx");

const HERE = __dirname;
const FONT = "Calibri";
const BODY = 22; // half-points: 11 pt
const LINE = 360; // 1.5 line spacing
const PAGE_W = 12240, PAGE_H = 15840, MARGIN = 1440;
const INK = "1F2A44";

// ---------------------------------------------------------------- read the chapters
const files = fs.readdirSync(path.join(HERE, "content")).filter((f) => f.endsWith(".md")).sort();
let text = files.map((f) => fs.readFileSync(path.join(HERE, "content", f), "utf8")).join("\n\n");
const refs = JSON.parse(fs.readFileSync(path.join(HERE, "references.json"), "utf8"));

// ---------------------------------------------------------------- number figures, tables, refs
const figIds = {}, tabIds = {};
let nFig = 0, nTab = 0;
for (const line of text.split("\n")) {
  const f = line.match(/^!\[.*\]\(.*\)\{.*#([\w-]+).*\}\s*$/);
  if (f) figIds[f[1]] = ++nFig;
  const t = line.match(/^Table:.*\{#([\w-]+)\}\s*$/);
  if (t) tabIds[t[1]] = ++nTab;
}
const refKeys = refs.map((r) => r.key);
const missing = [];
text = text
  .replace(/\{fig:([\w-]+)\}/g, (_, id) => (figIds[id] ? `Figure ${figIds[id]}` : (missing.push("fig:" + id), "Figure ??")))
  .replace(/\{tab:([\w-]+)\}/g, (_, id) => (tabIds[id] ? `Table ${tabIds[id]}` : (missing.push("tab:" + id), "Table ??")))
  .replace(/\{ref:([\w,\s-]+)\}/g, (_, keys) => "[" + keys.split(",").map((k) => {
    const i = refKeys.indexOf(k.trim());
    if (i < 0) missing.push("ref:" + k.trim());
    return i + 1;
  }).join(", ") + "]");
if (missing.length) {
  console.error("Unresolved references:", [...new Set(missing)].join(" "));
  process.exit(1);
}

// ---------------------------------------------------------------- inline formatting
function runs(s, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(new TextRun({ text: s.slice(last, m.index), ...base }));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(new TextRun({ text: tok.slice(2, -2), bold: true, ...base }));
    else if (tok.startsWith("`")) out.push(new TextRun({ text: tok.slice(1, -1), font: "Consolas", size: (base.size || BODY) - 2, ...base, ...{ font: "Consolas" } }));
    else out.push(new TextRun({ text: tok.slice(1, -1), italics: true, ...base }));
    last = m.index + tok.length;
  }
  if (last < s.length) out.push(new TextRun({ text: s.slice(last), ...base }));
  return out;
}

// ---------------------------------------------------------------- blocks
let landscape = false;
const sections = [];
let children = [];
function newSection(isLandscape) {
  if (children.length) sections.push({ landscape, children });
  children = [];
  landscape = isLandscape;
}
const contentWidth = () => (landscape ? PAGE_H : PAGE_W) - 2 * MARGIN;

function caption(label) {
  return new Paragraph({ style: "Caption", children: runs(label) });
}

function figure(cap, file, opts) {
  const p = path.join(HERE, file);
  const buf = fs.readFileSync(p);
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20); // PNG header
  const maxW = Math.min(parseFloat(opts.width || 6.5), contentWidth() / 1440) * 96;
  const maxH = (landscape ? 5.4 : 8.0) * 96;
  let dw = maxW, dh = (h / w) * maxW;
  if (dh > maxH) { dh = maxH; dw = (w / h) * maxH; }
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER, keepNext: true, spacing: { before: 120, after: 60, line: 240 },
    children: [new ImageRun({ type: "png", data: buf, transformation: { width: Math.round(dw), height: Math.round(dh) },
      altText: { title: cap, description: cap, name: path.basename(file) } })],
  }));
  children.push(caption(`Figure ${figIds[opts.id]}. ${cap}`));
}

function cellParas(s, header) {
  return s.split("<br>").map((part) => new Paragraph({
    spacing: { before: 30, after: 30, line: 252 },
    children: runs(part.trim(), { size: 19, bold: header || undefined, color: header ? "FFFFFF" : undefined }),
  }));
}

function table(cap, id, rows) {
  const cols = rows[0].length;
  // Column widths from content length, softened so short columns stay readable.
  const weights = [...Array(cols).keys()].map((c) => {
    const lens = rows.map((r) => (r[c] || "").replace(/[*`]/g, "").length);
    const avg = lens.reduce((a, b) => a + b, 0) / lens.length;
    return Math.max(Math.pow(avg, 0.8) + 0.15 * Math.pow(Math.max(...lens), 0.8), 4.5);
  });
  const total = contentWidth();
  // Each column is at least as wide as its longest word, so IDs and keys never break.
  const mins = [...Array(cols).keys()].map((c) => 200 + 105 * Math.max(...rows.map((r) =>
    Math.max(...(r[c] || "").replace(/[*`]/g, "").split(/\s+/).map((w) => w.length)))));
  const sum = weights.reduce((a, b) => a + b, 0);
  let widths = weights.map((w) => (w / sum) * total);
  for (let pass = 0; pass < 5; pass++) {
    const deficit = widths.reduce((d, w, c) => d + Math.max(0, mins[c] - w), 0);
    if (deficit < 1) break;
    const room = widths.reduce((r, w, c) => r + Math.max(0, w - mins[c]), 0);
    if (room <= deficit) break;
    widths = widths.map((w, c) => (w < mins[c] ? mins[c] : w - (deficit * (w - mins[c])) / room));
  }
  widths = widths.map(Math.floor);
  widths[cols - 1] += total - widths.reduce((a, b) => a + b, 0);
  const border = { style: BorderStyle.SINGLE, size: 4, color: "B8C0CC" };
  const borders = { top: border, bottom: border, left: border, right: border };
  children.push(caption(`Table ${tabIds[id]}. ${cap}`));
  children.push(new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((r, i) => new TableRow({
      tableHeader: i === 0, cantSplit: true,
      children: r.map((cell, c) => new TableCell({
        borders, width: { size: widths[c], type: WidthType.DXA },
        margins: { top: 40, bottom: 40, left: 90, right: 90 },
        shading: i === 0 ? { fill: "33415C", type: ShadingType.CLEAR, color: "auto" }
          : i % 2 === 0 ? { fill: "F4F6F9", type: ShadingType.CLEAR, color: "auto" } : undefined,
        children: cellParas(cell, i === 0),
      })),
    })),
  }));
  children.push(new Paragraph({ spacing: { before: 0, after: 120, line: 240 }, children: [] }));
}

const lines = text.split("\n");
let para = [];
function flush() {
  if (!para.length) return;
  children.push(new Paragraph({ style: "Body", children: runs(para.join(" ")) }));
  para = [];
}
let pendingTable = null;
let numberedLists = 0;
for (let i = 0; i < lines.length; i++) {
  const line = lines[i].replace(/\s+$/, "");
  if (!line.trim()) { flush(); continue; }
  if (line === "[[PAGEBREAK]]") { flush(); children.push(new Paragraph({ children: [new PageBreak()] })); continue; }
  if (line === "[[TOC]]") {
    flush();
    children.push(new Paragraph({ style: "TocTitle", children: [new TextRun("Table of Contents")] }));
    children.push(new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }));
    continue;
  }
  if (line === "[[LANDSCAPE]]") { flush(); newSection(true); continue; }
  if (line === "[[PORTRAIT]]") { flush(); newSection(false); continue; }
  let m;
  if ((m = line.match(/^(#{1,3}) (.*)$/))) {
    flush();
    const level = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][m[1].length - 1];
    children.push(new Paragraph({ heading: level, pageBreakBefore: m[1].length === 1, children: runs(m[2]) }));
    continue;
  }
  if ((m = line.match(/^!\[(.*)\]\((.*)\)\{(.*)\}$/))) {
    flush();
    const opts = {};
    for (const tok of m[3].trim().split(/\s+/)) {
      if (tok.startsWith("#")) opts.id = tok.slice(1);
      else { const [k, v] = tok.split("="); opts[k] = v; }
    }
    figure(m[1], m[2], opts);
    continue;
  }
  if ((m = line.match(/^Table: (.*) \{#([\w-]+)\}$/))) { flush(); pendingTable = { cap: m[1], id: m[2], rows: [] }; continue; }
  if (line.startsWith("|")) {
    flush();
    const cells = line.slice(1, -1).split("|").map((c) => c.trim());
    if (!pendingTable) throw new Error("Table without caption near: " + line);
    if (!cells.every((c) => /^:?-+:?$/.test(c))) pendingTable.rows.push(cells);
    if (!(lines[i + 1] || "").startsWith("|")) { table(pendingTable.cap, pendingTable.id, pendingTable.rows); pendingTable = null; }
    continue;
  }
  if ((m = line.match(/^> NOTE: (.*)$/))) {
    flush();
    const mark = { shading: { type: ShadingType.CLEAR, fill: "FFF0A0", color: "auto" } };
    children.push(new Paragraph({ style: "Note", children: [new TextRun({ text: "Team check: ", bold: true, ...mark }), ...runs(m[1], mark)] }));
    continue;
  }
  if ((m = line.match(/^- (.*)$/))) { flush(); children.push(new Paragraph({ style: "Body", numbering: { reference: "bullets", level: 0 }, spacing: { after: 60, line: LINE }, children: runs(m[1]) })); continue; }
  if ((m = line.match(/^\d+\. (.*)$/))) {
    flush();
    const prev = lines[i - 1] || "";
    if (!/^\d+\. /.test(prev)) numberedLists++;
    children.push(new Paragraph({ style: "Body", numbering: { reference: "numbers", level: 0, instance: numberedLists }, spacing: { after: 60, line: LINE }, children: runs(m[1]) }));
    continue;
  }
  para.push(line.trim());
}
flush();
newSection(landscape);

// ---------------------------------------------------------------- title page
const team = [
  ["Ethan Goedelman", "Product Owner; experimental design and statistics"],
  ["Ben Duffaut", "Scrum Master; full stack (CLI, database, dashboard)"],
  ["Gia Do", "Mutation operators"],
  ["Nathaniel Frank", "Execution engine and adapters"],
];
const center = (t, o = {}) => new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: o.after ?? 120, line: 276 }, children: [new TextRun({ text: t, font: FONT, size: o.size || 24, bold: o.bold, color: o.color || INK })] });
const titlePage = [
  new Paragraph({ spacing: { before: 1400 }, children: [] }),
  center("University of Central Florida", { size: 26 }),
  center("Department of Computer Science", { size: 24 }),
  center("COP 4934 Senior Design I, Fall 2026", { size: 24, after: 900 }),
  center("Mutation Testing for LLM Judges", { size: 52, bold: true, after: 200 }),
  center("Final Design Document", { size: 34, after: 1000 }),
  center("Group L13 (MTLJ)", { size: 26, bold: true, after: 200 }),
  ...team.map(([n, r]) => center(`${n}  |  ${r}`, { size: 22, after: 80 })),
  new Paragraph({ spacing: { after: 600 }, children: [] }),
  center("Sponsor: student-proposed project (student sponsor: Ethan Goedelman); no external sponsor", { size: 22 }),
  center("Instructor: Dr. Rick Leinecker", { size: 22 }),
  center("December 2026", { size: 22 }),
  new Paragraph({ children: [new PageBreak()] }),
];

// ---------------------------------------------------------------- document
const pageProps = (isLandscape) => ({
  page: {
    size: { width: PAGE_W, height: PAGE_H, orientation: isLandscape ? PageOrientation.LANDSCAPE : PageOrientation.PORTRAIT },
    margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN },
  },
});
const header = new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [new TextRun({ text: "MTLJ Final Design Document", size: 18, color: "6B7280", font: FONT })] })] });
const footer = new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "6B7280", font: FONT })] })] });

const docSections = sections.map((s, i) => ({
  properties: { ...pageProps(s.landscape), ...(i === 0 ? { titlePage: true } : {}) },
  headers: { default: header, first: new Header({ children: [] }) },
  footers: { default: footer, first: new Footer({ children: [] }) },
  children: i === 0 ? [...titlePage, ...s.children] : s.children,
}));

const heading = (size, before, after) => ({ run: { font: FONT, size, bold: true, color: INK }, paragraph: { spacing: { before, after, line: 276 }, keepNext: true, keepLines: true } });
const doc = new Document({
  creator: "Group L13 (MTLJ)",
  title: "Mutation Testing for LLM Judges: Final Design Document",
  features: { updateFields: true },
  styles: {
    default: { document: { run: { font: FONT, size: BODY } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Body", quickFormat: true, ...heading(32, 0, 200), paragraph: { ...heading(32, 0, 200).paragraph, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Body", quickFormat: true, ...heading(27, 300, 120), paragraph: { ...heading(27, 300, 120).paragraph, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Body", quickFormat: true, ...heading(23, 220, 80), paragraph: { ...heading(23, 220, 80).paragraph, outlineLevel: 2 } },
      { id: "Body", name: "Body", basedOn: "Normal", quickFormat: true, run: { font: FONT, size: BODY }, paragraph: { spacing: { after: 140, line: LINE } } },
      { id: "Caption", name: "Caption", basedOn: "Normal", run: { font: FONT, size: 20, italics: true, color: "374151" }, paragraph: { alignment: AlignmentType.CENTER, spacing: { before: 60, after: 200, line: 264 }, keepNext: false } },
      { id: "Note", name: "Review note", basedOn: "Normal", run: { font: FONT, size: 20 }, paragraph: { spacing: { before: 60, after: 160, line: 276 } } },
      { id: "TocTitle", name: "TOC Title", basedOn: "Normal", run: { font: FONT, size: 32, bold: true, color: INK }, paragraph: { spacing: { after: 240 } } },
    ],
  },
  numbering: {
    config: [
      { reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 270 } } } }] },
      { reference: "numbers", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } }] },
    ],
  },
  sections: [
    ...docSections,
    {
      properties: pageProps(false), headers: { default: header }, footers: { default: footer },
      children: [
        new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: false, children: [new TextRun("References")] }),
        ...refs.map((r, i) => new Paragraph({ style: "Body", indent: { left: 540, hanging: 540 }, spacing: { after: 100, line: 300 }, children: [new TextRun(`[${i + 1}]\t${r.text}`)] })),
      ],
    },
  ],
});

Packer.toBuffer(doc).then(async (raw) => {
  // Drop the ZIP's folder entries: Word has refused files with them when opened through a cloud service.
  const JSZip = require("jszip");
  const src = await JSZip.loadAsync(raw);
  const zip = new JSZip();
  for (const name of Object.keys(src.files)) {
    if (!src.files[name].dir) zip.file(name, await src.files[name].async("nodebuffer"), { createFolders: false });
  }
  const buf = await zip.generateAsync({ type: "nodebuffer", compression: "DEFLATE" });
  const out = path.join(HERE, "L13_MTLJ_Final_Design_Document.docx");
  fs.writeFileSync(out, buf);
  console.log(`Wrote ${out}: ${nFig} figures, ${nTab} tables, ${refs.length} references`);
});
