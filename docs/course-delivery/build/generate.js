const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  ImageRun, PageBreak, Header, Footer, PageNumber, NumberFormat,
  AlignmentType, HeadingLevel, WidthType, BorderStyle, ShadingType,
  SectionType, ExternalHyperlink, TableOfContents, TableLayoutType,
} = require("docx");
const fs = require("fs");
const path = require("path");

// ─── palette: DM-1 Deep Cyan (tech report) ───
const P = {
  bg: "162235", accent: "37DCF2",
  cover: { titleColor: "FFFFFF", subtitleColor: "B0B8C0", metaColor: "90989F", footerColor: "687078" },
  table: { headerBg: "1B6B7A", headerText: "FFFFFF", innerLine: "C8DDE2", surface: "EDF3F5" },
  primary: "0F3A47", body: "000000", secondary: "5A6B75",
};

const NB = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: NB, bottom: NB, left: NB, right: NB };
const allNoBorders = { top: NB, bottom: NB, left: NB, right: NB, insideHorizontal: NB, insideVertical: NB };

const SHOTS = path.join(__dirname, "..", "screenshots");

// ─── cover layout helpers (from design-system.md) ───
function splitTitleLines(title, charsPerLine) {
  if (title.length <= charsPerLine) return [title];
  const breakAfter = new Set([..."\uFF0C\u3002\u3001\uFF1B\uFF1A\uFF01\uFF1F", ..."\u7684\u4E0E\u548C\u53CA\u4E4B\u5728\u4E8E\u4E3A", ..."-_\u2014\u2013\u00B7/", ..." \t"]);
  const lines = [];
  let remaining = title;
  while (remaining.length > charsPerLine) {
    let breakAt = -1;
    for (let i = charsPerLine; i >= Math.floor(charsPerLine * 0.6); i--) {
      if (i < remaining.length && breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
    }
    if (breakAt === -1) {
      const limit = Math.min(remaining.length, Math.ceil(charsPerLine * 1.3));
      for (let i = charsPerLine + 1; i < limit; i++) {
        if (breakAfter.has(remaining[i - 1])) { breakAt = i; break; }
      }
    }
    if (breakAt === -1) {
      breakAt = charsPerLine;
      const prevChar = remaining[breakAt - 1], nextChar = remaining[breakAt];
      if (prevChar && nextChar && !breakAfter.has(prevChar) && !breakAfter.has(nextChar) &&
          /[\u4e00-\u9fff]/.test(prevChar) && /[\u4e00-\u9fff]/.test(nextChar)) breakAt -= 1;
    }
    lines.push(remaining.slice(0, breakAt).trim());
    remaining = remaining.slice(breakAt).trim();
  }
  if (remaining) lines.push(remaining);
  if (lines.length > 1 && lines[lines.length - 1].length <= 2) {
    const last = lines.pop();
    lines[lines.length - 1] += last;
  }
  return lines;
}

function calcTitleLayout(title, maxWidthTwips, preferredPt = 40, minPt = 24) {
  const charWidth = (pt) => pt * 20;
  const charsPerLine = (pt) => Math.floor(maxWidthTwips / charWidth(pt));
  let titlePt = preferredPt, lines;
  while (titlePt >= minPt) {
    const cpl = charsPerLine(titlePt);
    if (cpl < 2) { titlePt -= 2; continue; }
    lines = splitTitleLines(title, cpl);
    if (lines.length <= 3) break;
    titlePt -= 2;
  }
  if (!lines || lines.length > 3) {
    lines = splitTitleLines(title, charsPerLine(minPt));
    titlePt = minPt;
  }
  return { titlePt, titleLines: lines };
}

function calcCoverSpacing(params) {
  const { titleLineCount = 1, titlePt = 36, hasSubtitle = false, hasEnglishLabel = false,
    metaLineCount = 0, fixedHeight = 800, pageHeight = 16838, marginTop = 0, marginBottom = 0 } = params;
  const SAFETY = 1200;
  const usableHeight = pageHeight - marginTop - marginBottom - SAFETY;
  const titleHeight = titleLineCount * (titlePt * 23 + 200);
  const subtitleHeight = hasSubtitle ? (12 * 23 + 600) : 0;
  const englishLabelHeight = hasEnglishLabel ? (9 * 23 + 600) : 0;
  const metaHeight = metaLineCount * (10 * 23 + 100);
  const implicitParaHeight = 3 * 300;
  const contentHeight = titleHeight + subtitleHeight + englishLabelHeight + metaHeight + fixedHeight + implicitParaHeight;
  const remainingSpace = usableHeight - contentHeight;
  const safeRemaining = Math.max(remainingSpace, 400);
  const FOOTER_MIN = 800;
  const rawTop = Math.floor(safeRemaining * 0.45);
  const rawBottom = Math.floor(safeRemaining * 0.45);
  const bottomSpacing = Math.max(rawBottom, FOOTER_MIN);
  const topSpacing = Math.max(rawTop - Math.max(0, FOOTER_MIN - rawBottom), 400);
  const midSpacing = Math.max(safeRemaining - topSpacing - bottomSpacing, 0);
  return { topSpacing, midSpacing, bottomSpacing };
}

// ─── Recipe R1: Pure Paragraph Cover (left-aligned) ───
function buildCoverR1(config) {
  const C = config.palette;
  const padL = 1200, padR = 800;
  const availableWidth = 11906 - padL - padR - 300;
  const { titlePt, titleLines } = calcTitleLayout(config.title, availableWidth, 40, 24);
  const titleSize = titlePt * 2;
  const spacing = calcCoverSpacing({
    titleLineCount: titleLines.length, titlePt,
    hasSubtitle: !!config.subtitle, hasEnglishLabel: !!config.englishLabel,
    metaLineCount: (config.metaLines || []).length, fixedHeight: 400,
  });
  const accentLeft = { style: BorderStyle.SINGLE, size: 8, color: C.accent, space: 12 };
  const children = [];
  children.push(new Paragraph({ spacing: { before: spacing.topSpacing } }));
  if (config.englishLabel) {
    children.push(new Paragraph({
      indent: { left: padL, right: padR }, spacing: { after: 500 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: C.accent, space: 8 } },
      children: [new TextRun({ text: config.englishLabel.split("").join("  "), size: 18, color: C.accent, font: { ascii: "Calibri", eastAsia: "SimHei" }, characterSpacing: 40 })],
    }));
  }
  for (let i = 0; i < titleLines.length; i++) {
    children.push(new Paragraph({
      indent: { left: padL },
      spacing: { after: i < titleLines.length - 1 ? 100 : 300, line: Math.ceil(titlePt * 23), lineRule: "atLeast" },
      children: [new TextRun({ text: titleLines[i], size: titleSize, bold: true, color: C.titleColor, font: { eastAsia: "SimHei", ascii: "Arial" } })],
    }));
  }
  if (config.subtitle) {
    children.push(new Paragraph({
      indent: { left: padL }, spacing: { after: 800 },
      children: [new TextRun({ text: config.subtitle, size: 24, color: C.subtitleColor, font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    }));
  }
  for (const line of (config.metaLines || [])) {
    children.push(new Paragraph({
      indent: { left: padL + 200 }, spacing: { after: 80 },
      border: { left: accentLeft },
      children: [new TextRun({ text: line, size: 24, color: C.metaColor, font: { eastAsia: "Microsoft YaHei", ascii: "Arial" } })],
    }));
  }
  children.push(new Paragraph({ spacing: { before: spacing.bottomSpacing } }));
  children.push(new Paragraph({
    indent: { left: padL, right: padR },
    border: { top: { style: BorderStyle.SINGLE, size: 2, color: C.accent, space: 8 } },
    spacing: { before: 200 },
    children: [
      new TextRun({ text: config.footerLeft || "", size: 16, color: C.footerColor, font: { ascii: "Arial", eastAsia: "Microsoft YaHei" } }),
      new TextRun({ text: "                                        " }),
      new TextRun({ text: config.footerRight || "", size: 16, color: C.footerColor, font: { ascii: "Arial", eastAsia: "Microsoft YaHei" } }),
    ],
  }));
  return [new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    layout: TableLayoutType.FIXED,
    borders: allNoBorders,
    rows: [new TableRow({
      height: { value: 16838, rule: "exact" },
      children: [new TableCell({
        shading: { type: ShadingType.CLEAR, fill: config.bg }, borders: noBorders,
        verticalAlign: "top",
        children,
      })],
    })],
  })];
}

// ─── body component builders ───
function h1(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_1,
    keepNext: true,
    spacing: { before: 360, after: 160, line: 380, lineRule: "atLeast" },
    children: [new TextRun({ text, bold: true, size: 32, color: P.primary, font: { ascii: "Times New Roman", eastAsia: "SimHei" } })],
  });
}
function h2(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    keepNext: true,
    spacing: { before: 280, after: 120, line: 340, lineRule: "atLeast" },
    children: [new TextRun({ text, bold: true, size: 28, color: P.primary, font: { ascii: "Times New Roman", eastAsia: "SimHei" } })],
  });
}
function h3(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_3,
    keepNext: true,
    spacing: { before: 220, after: 100, line: 312 },
    children: [new TextRun({ text, bold: true, size: 24, color: P.primary, font: { ascii: "Times New Roman", eastAsia: "SimHei" } })],
  });
}
function body(runsOrText, opts = {}) {
  const runs = typeof runsOrText === "string"
    ? [new TextRun({ text: runsOrText, size: 24, color: P.body, font: { ascii: "Times New Roman", eastAsia: "SimSun" } })]
    : runsOrText;
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    indent: { firstLine: 480 },
    spacing: { line: 312, after: opts.after ?? 80 },
    children: runs,
  });
}
function bt(text, extra = {}) {
  return new TextRun({ text, size: 24, color: P.body, font: { ascii: "Times New Roman", eastAsia: "SimSun" }, ...extra });
}
function bullet(text) {
  return new Paragraph({
    bullet: { level: 0 },
    spacing: { line: 312, after: 40 },
    children: [bt(text)],
  });
}
function caption(text) {
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 60, after: 240, line: 280 },
    children: [new TextRun({ text, size: 21, color: P.secondary, font: { ascii: "Times New Roman", eastAsia: "SimSun" } })],
  });
}
function tableTitle(text) {
  return new Paragraph({
    keepNext: true,
    alignment: AlignmentType.CENTER,
    spacing: { before: 200, after: 100, line: 280 },
    children: [new TextRun({ text, bold: true, size: 21, color: P.primary, font: { ascii: "Times New Roman", eastAsia: "SimHei" } })],
  });
}

// horizontal-only business table
function makeTable(headers, rows, widths) {
  const headerRow = new TableRow({
    tableHeader: true, cantSplit: true,
    children: headers.map((text, i) => new TableCell({
      children: [new Paragraph({ spacing: { line: 280 }, children: [new TextRun({ text, bold: true, size: 21, color: P.table.headerText, font: { ascii: "Times New Roman", eastAsia: "SimHei" } })] })],
      shading: { type: ShadingType.CLEAR, fill: P.table.headerBg },
      margins: { top: 80, bottom: 80, left: 120, right: 120 },
      width: { size: widths[i], type: WidthType.PERCENTAGE },
    })),
  });
  const dataRows = rows.map((cells, r) => new TableRow({
    cantSplit: true,
    children: cells.map((text, i) => new TableCell({
      children: [new Paragraph({ spacing: { line: 280 }, children: [new TextRun({ text: String(text), size: 21, color: P.body, font: { ascii: "Times New Roman", eastAsia: "SimSun" } })] })],
      shading: r % 2 === 1 ? { type: ShadingType.CLEAR, fill: P.table.surface } : undefined,
      margins: { top: 70, bottom: 70, left: 120, right: 120 },
      width: { size: widths[i], type: WidthType.PERCENTAGE },
    })),
  }));
  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 4, color: P.table.headerBg },
      bottom: { style: BorderStyle.SINGLE, size: 4, color: P.table.headerBg },
      left: NB, right: NB,
      insideHorizontal: { style: BorderStyle.SINGLE, size: 1, color: P.table.innerLine },
      insideVertical: NB,
    },
    rows: [headerRow, ...dataRows],
  });
}

// image with fixed display width, aspect preserved
const _is = require("image-size");
const sizeOf = _is.imageSize || _is.default || _is;
function figure(fileName, displayWidth) {
  const p = path.join(SHOTS, fileName);
  const buf = fs.readFileSync(p);
  const dim = sizeOf(buf);
  const w = displayWidth;
  const h = Math.round(w * dim.height / dim.width);
  return new Paragraph({
    keepNext: true,
    alignment: AlignmentType.CENTER,
    spacing: { before: 160, after: 0 },
    children: [new ImageRun({ data: buf, transformation: { width: w, height: h }, type: "png" })],
  });
}

// ─── content data ───
const repoStats = [
  ["\u4E3B\u5206\u652F\u63D0\u4EA4\u6570", "131 \u6B21"],
  ["\u5DF2\u5408\u5E76 Pull Request", "45 \u4E2A"],
  ["\u5F00\u653E Issue\uFF08\u5747\u4E3A\u7B2C\u56DB\u9636\u6BB5\u4EFB\u52A1\uFF09", "78 \u4E2A"],
  ["Python \u6E90\u6587\u4EF6", "76 \u4E2A"],
  ["JavaScript \u6587\u4EF6", "18 \u4E2A"],
  ["HTML \u6A21\u677F", "40 \u4E2A"],
  ["CSS \u6837\u5F0F\u6587\u4EF6", "5 \u4E2A"],
  ["\u8FC7\u7A0B\u6587\u6863\uFF08docs/ \u76EE\u5F55\uFF0CMarkdown\uFF09", "27 \u7BC7"],
  ["\u81EA\u52A8\u5316\u6D4B\u8BD5\u6A21\u5757", "Django 8 \u4E2A + \u6D4F\u89C8\u5668\u7AEF E2EE \u6D4B\u8BD5 2 \u4E2A"],
];

const docList = [
  ["\u9700\u6C42\u4E0E\u89C4\u5212", "iChat Pro \u9700\u6C42\u6587\u6863\uFF08\u4FEE\u8BA2\u7248\uFF09"],
  ["\u9700\u6C42\u4E0E\u89C4\u5212", "iChat Pro Phase \u89C4\u5212\u4E0E\u4E00\u671F\u4EA4\u4ED8\u5BA1\u67E5\u6587\u6863"],
  ["\u9700\u6C42\u4E0E\u89C4\u5212", "iChat Pro T32 \u4E00\u671F\u6587\u6863\u5BF9\u9F50\u603B\u7ED3"],
  ["\u8BBE\u8BA1\u89C4\u8303", "iChat Pro \u524D\u7AEF\u8BBE\u8BA1\u89C4\u8303\u6587\u6863"],
  ["\u8BBE\u8BA1\u89C4\u8303", "iChat Pro \u540E\u7AEF\u8BBE\u8BA1\u89C4\u8303\u6587\u6863"],
  ["\u8BBE\u8BA1\u89C4\u8303", "iChat Pro \u6570\u636E\u5E93\u8BBE\u8BA1\u89C4\u8303\u6587\u6863"],
  ["\u8BBE\u8BA1\u89C4\u8303", "iChat Pro \u6280\u672F\u6808"],
  ["\u5B89\u5168\u4E0E\u534F\u8BAE", "iChat Pro \u5B9E\u65F6\u901A\u4FE1\u4E0E\u7AEF\u5230\u7AEF\u52A0\u5BC6\u6D88\u606F\u534F\u8BAE\u8BBE\u8BA1\u6587\u6863"],
  ["\u5B89\u5168\u4E0E\u534F\u8BAE", "iChat Pro \u7AEF\u5230\u7AEF\u52A0\u5BC6\u901A\u4FE1\u8BBE\u8BA1\u6587\u6863"],
  ["\u5B89\u5168\u4E0E\u534F\u8BAE", "iChat Pro \u6D4F\u89C8\u5668\u7AEF\u5B89\u5168\u5A01\u80C1\u6A21\u578B"],
  ["\u5B89\u5168\u4E0E\u534F\u8BAE", "iChat Pro \u90E8\u7F72\u5B89\u5168\u8BF4\u660E"],
  ["\u5B89\u5168\u4E0E\u534F\u8BAE", "iChat Pro \u6587\u4EF6\u4F20\u8F93\u89C4\u8303"],
  ["\u63A5\u53E3\u4E0E\u5C55\u793A", "iChat Pro API \u63A5\u53E3\u6587\u6863"],
  ["\u63A5\u53E3\u4E0E\u5C55\u793A", "iChat Pro UML \u4E0E\u67B6\u6784\u56FE\u4EA4\u4ED8\u6587\u6863"],
  ["\u63A5\u53E3\u4E0E\u5C55\u793A", "iChat Pro \u7CFB\u7EDF\u6027\u4ECB\u7ECD\u6587\u6863"],
  ["\u63A5\u53E3\u4E0E\u5C55\u793A", "iChat Pro \u6F14\u793A\u6307\u5357"],
  ["\u4E13\u9879\u65B9\u6848", "iChat Pro Bot\u3001LLM Agent \u4E0E Channel \u6269\u5C55\u65B9\u6848\u6587\u6863"],
  ["\u4E13\u9879\u65B9\u6848", "iChat Pro Twemoji \u8868\u60C5\u6E32\u67D3\u65B9\u6848\u6587\u6863"],
  ["\u4E13\u9879\u65B9\u6848", "iChat Pro \u7FA4\u7EC4\u7BA1\u7406\u4E0E\u9080\u8BF7\u6D41\u7A0B\u5B9E\u73B0\u6587\u6863"],
  ["\u4E13\u9879\u65B9\u6848", "iChat Pro \u8F6C\u53D1\u754C\u9762\u4E0E\u6587\u4EF6\u8F6C\u53D1\u95EE\u9898\u68B3\u7406"],
  ["\u9636\u6BB5\u9A8C\u6536", "iChat Pro Phase 2 \u9A8C\u6536\u624B\u518C"],
  ["\u9636\u6BB5\u9A8C\u6536", "iChat Pro Phase 3 \u6F14\u793A\u811A\u672C\u4E0E\u9A8C\u6536\u6587\u6863"],
  ["\u9636\u6BB5\u9A8C\u6536", "iChat Pro AI Assistant \u589E\u5F3A\u6574\u7406\u6587\u6863"],
  ["\u9636\u6BB5\u9A8C\u6536", "iChat Pro \u7FA4\u7EC4\u5DE6\u4FA7\u9762\u677F\u6574\u5408\u8BF4\u660E\u6587\u6863"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "iChat Pro \u8F6F\u4EF6\u9700\u6C42\u89C4\u683C\u8BF4\u660E\u4E66"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "\u7B2C\u4E00\u6B21\u5B9E\u9A8C-\u9700\u6C42\u5206\u6790\u4E0E\u4EFB\u52A1\u62C6\u89E3\u62A5\u544A"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "\u7B2C\u4E8C\u6B21\u5B9E\u9A8C-\u8F6F\u4EF6\u5DE5\u7A0B\u5168\u94FE\u8DEF\u4E0E AI \u5B9E\u8DF5\u62A5\u544A"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "\u5C0F\u7EC4\u5206\u5DE5\u8868\uFF08\u8F6F\u4EF6\u6D4B\u8BD5\u5B9E\u8DF5\uFF09"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "\u4EE3\u7801\u89C4\u8303\u3001\u7248\u672C\u7BA1\u7406\u4E0E\u6D4B\u8BD5\u5DE5\u5177\u8BF4\u660E"],
  ["\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5", "AI \u5B9E\u8DF5\u8BB0\u5F55"],
];

const members = [
  ["\u53F6\u4F1F\u667A \uFF08HeanX\uFF09", "121052024140", "\u7EC4\u957F / \u524D\u7AEF\u4E0E\u4EA4\u4ED8\u8D1F\u8D23\u4EBA", "80", "\u9879\u76EE\u7EDF\u7B79\u4E0E PR \u8BC4\u5BA1\u5408\u5E76\uFF1B\u524D\u7AEF\u6574\u4F53\u67B6\u6784\u4E0E\u9875\u9762\u91CD\u6784\uFF1B\u4F1A\u8BDD\u4E0E\u6D88\u606F\u64CD\u4F5C\u540E\u7AEF\u3001\u8054\u7CFB\u4EBA\u641C\u7D22\uFF1BUML \u4E0E\u6F14\u793A\u6587\u6863\uFF1BAI \u52A9\u624B\u57FA\u7840\uFF1B\u5B89\u5168\u52A0\u56FA"],
  ["\u8BB8\u6822\u5883 \uFF08AEther61\uFF09", "121052024142", "\u540E\u7AEF\uFF08\u6570\u636E\u6A21\u578B\u3001\u8D26\u53F7\u4E0E\u9690\u79C1\uFF09", "37", "\u804A\u5929\u6570\u636E\u6A21\u578B\uFF1B\u7FA4\u7EC4\u6A21\u578B\u6574\u5408\u4E0E\u6743\u9650\u4FEE\u590D\uFF1B\u9690\u79C1\u3001\u62C9\u9ED1\u3001\u591A\u8D26\u53F7\u3001\u4F1A\u8BDD\u7BA1\u7406\u7B49 API\uFF1BWebSocket \u6CBB\u7406\uFF1BAI \u72B6\u6001\u7AEF\u70B9\u4E0E\u7CFB\u7EDF\u63D0\u793A\u8BCD"],
  ["\u5E9E\u5609\u94ED \uFF08pislan\uFF09", "121052024146", "\u524D\u7AEF\uFF08\u8BBE\u7F6E\u4E2D\u5FC3\uFF09", "7", "\u8BBE\u7F6E\u4E2D\u5FC3\u516D\u4E2A\u9875\u9762\uFF1B\u4FA7\u8FB9\u680F\u591A\u89C6\u56FE\u5BFC\u822A\uFF1BPhase 2 \u4EBA\u5DE5\u9A8C\u6536\u6E05\u5355"],
  ["\u6797\u5F18 \uFF08ketter1024\uFF09", "121052024144", "\u540E\u7AEF\uFF08\u8D26\u53F7\u4F53\u7CFB\u4E0E\u914D\u7F6E\uFF09", "7", "accounts \u5E94\u7528\uFF08\u8D44\u6599\u3001\u8BA4\u8BC1\u3001\u8054\u7CFB\u4EBA\u3001\u5BA2\u6237\u7AEF\u5BC6\u94A5\uFF09\uFF1B\u4F9D\u8D56\u4E0E\u914D\u7F6E"],
];

const workPackages = [
  ["\u9879\u76EE\u7EDF\u7B79\u4E0E\u9700\u6C42\u57FA\u7EBF", "P4 T01\u2013T03", "#128\u2013#130", "3", "\u9700\u6C42\u57FA\u7EBF\u3001\u8FED\u4EE3\u8BA1\u5212\u3001\u98CE\u9669\u6E05\u5355"],
  ["\u4E91\u7AEF\u67B6\u6784\u4E0E\u90E8\u7F72\u57FA\u7840", "P4 T04\u2013T13", "#131\u2013#140", "10", "\u57DF\u540D\u3001HTTPS/WSS\u3001ASGI\u3001PostgreSQL\u3001Redis\u3001\u5907\u4EFD"],
  ["Windows \u684C\u9762\u7AEF", "P4 T14\u2013T21", "#141\u2013#148", "8", "\u5B89\u88C5\u5305\u3001\u7CFB\u7EDF\u6258\u76D8\u4E0E\u901A\u77E5\u3001\u5B89\u5168\u5B58\u50A8"],
  ["Android \u79FB\u52A8\u7AEF", "P4 T22\u2013T30", "#149\u2013#157", "9", "APK \u6784\u5EFA\u3001\u901A\u77E5\u9002\u914D\u3001\u751F\u547D\u5468\u671F\u7BA1\u7406"],
  ["\u6D88\u606F\u53EF\u9760\u6027\u4E0E\u540E\u7AEF\u589E\u5F3A", "P4 T31\u2013T37", "#158\u2013#164", "7", "\u6D88\u606F\u72B6\u6001\u673A\u3001\u5E42\u7B49\u3001\u79BB\u7EBF\u8865\u53D6\u3001\u5F31\u7F51\u6062\u590D"],
  ["E2EE \u4E0E\u5B89\u5168\u589E\u5F3A", "P4 T38\u2013T43", "#165\u2013#170", "6", "\u8BBE\u5907\u8EAB\u4EFD\u6A21\u578B\u3001\u591A\u8BBE\u5907\u5BC6\u94A5\u5206\u53D1\u3001\u5A01\u80C1\u6A21\u578B"],
  ["CI/CD \u4E0E\u8D28\u91CF\u5DE5\u7A0B", "P4 T44\u2013T50", "#171\u2013#177", "7", "\u9759\u6001\u68C0\u67E5\u3001\u8986\u76D6\u7387\u95E8\u7981\u3001\u6784\u5EFA\u4E0E\u56DE\u6EDA"],
  ["AI \u5B9E\u8DF5\u4E0E\u8FC7\u7A0B\u8D44\u4EA7", "P4 T51\u2013T54", "#178\u2013#181", "4", "AI \u4F7F\u7528\u8BB0\u5F55\u3001\u7F3A\u9677\u5206\u6790\u3001\u6700\u7EC8\u6750\u6599"],
  ["\u4EA7\u4E1A\u7EA7\u6D4B\u8BD5", "P4 Test T01\u2013T24", "#182\u2013#205", "24", "\u6D4B\u8BD5\u8BA1\u5212\u3001\u9ED1\u767D\u76D2\u3001\u517C\u5BB9\u3001\u5B89\u5168\u3001\u6027\u80FD\u6D4B\u8BD5"],
];

// ─── body content ───
const bodyChildren = [];

// 报告摘要
bodyChildren.push(h1("\u62A5\u544A\u6458\u8981"));
bodyChildren.push(body("\u672C\u62A5\u544A\u4E3A iChat Pro \u9879\u76EE\u7684\u8BFE\u7A0B\u4EA4\u4ED8\u6750\u6599\uFF0C\u6C47\u603B\u9879\u76EE\u5F53\u524D\u5B8C\u6210\u60C5\u51B5\u3001\u4EE3\u7801\u7BA1\u7406\u5E73\u53F0\u4FE1\u606F\u3001\u5C0F\u7EC4\u6210\u5458\u5206\u5DE5\u4E0E\u4E2A\u4EBA\u5DE5\u4F5C\u5C0F\u7ED3\uFF0C\u4EE5\u53CA\u5C0F\u7EC4\u540E\u7EED\u8BA1\u5212\u89E3\u51B3\u7684\u95EE\u9898\u3002"));
bodyChildren.push(body("iChat Pro \u662F\u4E00\u4E2A\u57FA\u4E8E Django \u4E0E WebSocket \u7684\u7AEF\u5230\u7AEF\u52A0\u5BC6\uFF08End-to-End Encryption\uFF0CE2EE\uFF09\u5373\u65F6\u901A\u4FE1\u7CFB\u7EDF\uFF0C\u5DF2\u5B9E\u73B0\u8D26\u53F7\u4F53\u7CFB\u3001\u8054\u7CFB\u4EBA\u3001\u79C1\u804A\u4E0E\u7FA4\u804A\u3001\u6D88\u606F\u52A0\u5BC6\u6536\u53D1\u3001\u6587\u4EF6\u4F20\u8F93\u3001\u4F1A\u8BDD\u7BA1\u7406\u3001\u8BBE\u7F6E\u4E2D\u5FC3\u4E0E AI \u52A9\u624B\u7B49\u6838\u5FC3\u529F\u80FD\u3002\u9879\u76EE\u4EE3\u7801\u5DF2\u5B8C\u6210\u5E76\u5168\u90E8\u6258\u7BA1\u4E8E GitHub\uFF08github.com/HeanX/iChat_Pro\uFF09\uFF0C\u4E3B\u5206\u652F\u7D2F\u8BA1\u63D0\u4EA4 131 \u6B21\uFF0C\u5408\u5E76 Pull Request 45 \u4E2A\uFF1Bdocs \u76EE\u5F55\u6C89\u6DC0\u5F00\u53D1\u8FC7\u7A0B\u6587\u6863 27 \u7BC7\uFF0C\u8986\u76D6\u9700\u6C42\u3001\u8BBE\u8BA1\u3001\u5B89\u5168\u3001\u6D4B\u8BD5\u4E0E\u9A8C\u6536\u7B49\u73AF\u8282\u3002\u5C0F\u7EC4\u4EE5 GitHub Issue \u9A71\u52A8\u5F00\u53D1\uFF0C\u6210\u5458\u6309\u6A21\u5757\u5206\u5DE5\u534F\u4F5C\u3002"));
bodyChildren.push(body("\u9879\u76EE\u4E0B\u4E00\u9636\u6BB5\u7684 78 \u9879\u4EFB\u52A1\u5DF2\u5168\u90E8\u5EFA\u6210 GitHub Issue\uFF08\u7B2C\u56DB\u9636\u6BB5\uFF09\uFF0C\u56F4\u7ED5\u4E91\u7AEF\u90E8\u7F72\u3001\u8DE8\u5E73\u53F0\u5BA2\u6237\u7AEF\u3001\u6D88\u606F\u53EF\u9760\u6027\u3001\u5B89\u5168\u589E\u5F3A\u4E0E\u4EA7\u4E1A\u7EA7\u6D4B\u8BD5\u7B49\u4E5D\u5927\u5DE5\u4F5C\u5305\u5C55\u5F00\uFF0C\u5F85\u5C0F\u7EC4\u7EDF\u4E00\u5206\u5DE5\u540E\u9010\u6B65\u5B9E\u65BD\u3002", { after: 160 }));

// 1 项目概述
bodyChildren.push(h1("1  \u9879\u76EE\u6982\u8FF0"));
bodyChildren.push(body("iChat Pro \u662F\u4E00\u4E2A\u9762\u5411\u8BFE\u7A0B\u5C0F\u7EC4\u4F5C\u4E1A\u4EA4\u4ED8\u7684\u8F7B\u91CF\u7EA7\u5B89\u5168\u5373\u65F6\u901A\u4FE1\u9879\u76EE\u3002\u7CFB\u7EDF\u4EE5\u96F6\u77E5\u8BC6\u67B6\u6784\u4E3A\u8BBE\u8BA1\u76EE\u6807\uFF0C\u6D88\u606F\u5728\u6D4F\u89C8\u5668\u7AEF\u5B8C\u6210\u52A0\u5BC6\u4E0E\u89E3\u5BC6\uFF0C\u670D\u52A1\u5668\u4EC5\u4E2D\u8F6C\u5BC6\u6587\uFF0C\u4E0D\u5B58\u50A8\u660E\u6587\u5185\u5BB9\uFF1B\u5BC6\u94A5\u534F\u5546\u91C7\u7528 ECDH P-256\uFF0C\u6D88\u606F\u52A0\u5BC6\u91C7\u7528 AES-256-GCM\uFF0C\u5E76\u63D0\u4F9B\u5BC6\u94A5\u6307\u7EB9\u6838\u9A8C\u673A\u5236\u4F9B\u7528\u6237\u6838\u5BF9\u901A\u4FE1\u5BF9\u8C61\u8EAB\u4EFD\u3002"));
bodyChildren.push(body("\u9879\u76EE\u4E3B\u8981\u529F\u80FD\u5982\u4E0B\uFF1A", { after: 40 }));
bodyChildren.push(bullet("\u7528\u6237\u6CE8\u518C\u3001\u767B\u5F55\u3001\u767B\u51FA\u548C\u4E2A\u4EBA\u8D44\u6599\u7BA1\u7406\uFF0C\u652F\u6301\u7528\u6237\u540D\u5BC6\u7801\u4E0E\u626B\u7801\u4E24\u79CD\u767B\u5F55\u65B9\u5F0F\uFF1B"));
bodyChildren.push(bullet("\u8054\u7CFB\u4EBA\u5173\u7CFB\u3001\u79C1\u804A\u4F1A\u8BDD\u521B\u5EFA\u548C\u4F1A\u8BDD\u5217\u8868\uFF1B"));
bodyChildren.push(bullet("\u7FA4\u804A\u521B\u5EFA\u3001\u6210\u5458\u7BA1\u7406\u3001\u9080\u8BF7\u3001\u516C\u544A\u548C\u9759\u97F3\uFF1B"));
bodyChildren.push(bullet("\u57FA\u4E8E Django Channels \u7684 WebSocket \u5B9E\u65F6\u6D88\u606F\u6536\u53D1\uFF0C\u79C1\u804A\u4E0E\u7FA4\u804A\u5747\u652F\u6301\u7AEF\u5230\u7AEF\u52A0\u5BC6\uFF1B"));
bodyChildren.push(bullet("\u6587\u4EF6\u4F20\u8F93\u3001\u52A0\u5BC6\u6587\u4EF6\u5BC6\u94A5\u5206\u53D1\u548C\u6D88\u606F\u8F6C\u53D1\uFF1B"));
bodyChildren.push(bullet("\u6D88\u606F\u5DF2\u9001\u8FBE\u3001\u5DF2\u8BFB\u3001\u64A4\u56DE\u3001\u5220\u9664\u548C\u81EA\u52A8\u6E05\u7406\uFF1B"));
bodyChildren.push(bullet("\u641C\u7D22\u3001\u901A\u77E5\u3001\u9690\u79C1\u4E0E\u5B89\u5168\u3001\u6570\u636E\u4E0E\u5B58\u50A8\u7B49\u8BBE\u7F6E\u9875\u9762\uFF1B"));
bodyChildren.push(bullet("AI Assistant \u914D\u7F6E\u4E0E\u5BF9\u8BDD\u9762\u677F\uFF08\u63A5\u5165\u56FD\u4EA7\u5927\u6A21\u578B qwen-plus\uFF09\uFF1B"));
bodyChildren.push(bullet("Electron \u684C\u9762\u5BA2\u6237\u7AEF\u5305\u88C5\u3002"));
bodyChildren.push(body("\u6280\u672F\u6808\u65B9\u9762\uFF0C\u540E\u7AEF\u4F7F\u7528 Python 3.13 \u4E0E Django\uFF0C\u5B9E\u65F6\u901A\u4FE1\u4F7F\u7528 Django Channels \u63D0\u4F9B\u7684 WebSocket \u901A\u9053\uFF0C\u6570\u636E\u5B58\u50A8\u4F7F\u7528 SQLite\uFF1B\u524D\u7AEF\u4F7F\u7528\u539F\u751F HTML/CSS/JavaScript \u5E76\u5F15\u5165 Tailwind CSS \u6784\u5EFA\u754C\u9762\uFF0C\u684C\u9762\u7AEF\u901A\u8FC7 Node.js \u4E0E Electron \u5B9E\u73B0\u8DE8\u5E73\u53F0\u5305\u88C5\u3002", { after: 160 }));

// 2 项目完成情况
bodyChildren.push(h1("2  \u9879\u76EE\u5B8C\u6210\u60C5\u51B5"));
bodyChildren.push(h2("2.1  \u4EE3\u7801\u5B8C\u6210\u4E0E\u4E0A\u4F20\u60C5\u51B5"));
bodyChildren.push(body("\u9879\u76EE\u4EE3\u7801\u5DF2\u57FA\u672C\u5B8C\u6210\u5E76\u5168\u90E8\u4E0A\u4F20\u81F3 GitHub \u8FDC\u7A0B\u4ED3\u5E93\uFF0C\u4E3B\u5206\u652F\u4FDD\u6301\u53EF\u6784\u5EFA\u3001\u53EF\u8FD0\u884C\u72B6\u6001\u3002\u4ED3\u5E93\u5185\u7F6E\u6F14\u793A\u6570\u636E\u811A\u672C demo_setup.py\uFF0C\u53EF\u4E00\u952E\u521B\u5EFA\u4E09\u4E2A\u6F14\u793A\u8D26\u53F7\uFF08alice\u3001bob\u3001carol\uFF09\u5E76\u5EFA\u7ACB\u8054\u7CFB\u4EBA\u5173\u7CFB\uFF0C\u4FBF\u4E8E\u9A8C\u6536\u65B9\u5FEB\u901F\u590D\u73B0\u6F14\u793A\u73AF\u5883\u3002\u4ED3\u5E93\u89C4\u6A21\u7EDF\u8BA1\u5982\u8868 2-1 \u6240\u793A\u3002"));
bodyChildren.push(tableTitle("\u8868 2-1  \u4EE3\u7801\u4ED3\u5E93\u89C4\u6A21\u7EDF\u8BA1"));
bodyChildren.push(makeTable(["\u7EDF\u8BA1\u6307\u6807", "\u6570\u503C"], repoStats, [55, 45]));
bodyChildren.push(body("\u6D4B\u8BD5\u65B9\u9762\uFF0Caccounts \u5E94\u7528\u4E0E chat \u5E94\u7528\u4E0B\u5171 8 \u4E2A Django \u6D4B\u8BD5\u6A21\u5757\uFF0C\u8986\u76D6\u6838\u5FC3\u4E1A\u52A1\u903B\u8F91\u3001\u4F1A\u8BDD API\u3001\u7FA4\u7EC4\u5B9E\u65F6\u901A\u4FE1\u3001\u9690\u79C1\u6743\u9650\u3001Phase 2 \u540E\u7AEF\u4E0E LLM \u96C6\u6210\u7B49\u573A\u666F\uFF1B\u53E6\u6709 2 \u4E2A\u8FD0\u884C\u5728 Node.js \u73AF\u5883\u7684\u6D4F\u89C8\u5668\u7AEF\u7AEF\u5230\u7AEF\u52A0\u5BC6\u6D4B\u8BD5\u811A\u672C\uFF08private_chat_e2ee\u3001group_chat_e2ee\uFF09\uFF0C\u9A8C\u8BC1\u5BC6\u94A5\u534F\u5546\u4E0E\u52A0\u5BC6\u6536\u53D1\u94FE\u8DEF\u7684\u6B63\u786E\u6027\u3002", { after: 120 }));

bodyChildren.push(h2("2.2  \u7CFB\u7EDF\u754C\u9762\u622A\u56FE"));
bodyChildren.push(body("\u4EE5\u4E0B\u622A\u56FE\u5747\u6765\u81EA\u672C\u5730\u8FD0\u884C\u73AF\u5883\uFF08Django \u5F00\u53D1\u670D\u52A1\u5668 + \u6F14\u793A\u8D26\u53F7\uFF09\uFF0C\u6DB5\u76D6\u767B\u5F55\u3001\u5DE5\u4F5C\u53F0\u3001\u8054\u7CFB\u4EBA\u3001\u7FA4\u7EC4\u3001\u79C1\u804A\u4E0E\u7FA4\u804A\u52A0\u5BC6\u4F1A\u8BDD\u3001\u8BBE\u7F6E\u4E2D\u5FC3\u4EE5\u53CA AI \u52A9\u624B\u7B49\u6838\u5FC3\u754C\u9762\u3002"));
bodyChildren.push(figure("01-login-qr.png", 480));
bodyChildren.push(caption("\u56FE 2-1  \u626B\u7801\u767B\u5F55\u9875\uFF08\u591A\u8BBE\u5907\u5173\u8054\u5165\u53E3\uFF09"));
bodyChildren.push(figure("02-login-form.png", 480));
bodyChildren.push(caption("\u56FE 2-2  \u7528\u6237\u540D\u5BC6\u7801\u767B\u5F55\u9875"));
bodyChildren.push(figure("03-main-interface.png", 480));
bodyChildren.push(caption("\u56FE 2-3  \u5DE5\u4F5C\u53F0\u4E3B\u754C\u9762\uFF08\u4F1A\u8BDD\u5217\u8868\u4E0E\u52A0\u5BC6\u4FE1\u606F\u9762\u677F\uFF09"));
bodyChildren.push(figure("04-contacts.png", 480));
bodyChildren.push(caption("\u56FE 2-4  \u8054\u7CFB\u4EBA\u7BA1\u7406\u9875"));
bodyChildren.push(figure("05-groups.png", 480));
bodyChildren.push(caption("\u56FE 2-5  \u7FA4\u7EC4\u8BE6\u60C5\u9875\uFF08\u6210\u5458\u7BA1\u7406\u4E0E\u9080\u8BF7\uFF09"));
bodyChildren.push(figure("06-chat-e2ee.png", 480));
bodyChildren.push(caption("\u56FE 2-6  \u79C1\u804A\u7AEF\u5230\u7AEF\u52A0\u5BC6\u4F1A\u8BDD\uFF08\u53CC\u7AEF\u6D88\u606F\u4E0E\u9001\u8FBE\u72B6\u6001\uFF09"));
bodyChildren.push(figure("07-group-chat.png", 480));
bodyChildren.push(caption("\u56FE 2-7  \u7FA4\u804A\u7AEF\u5230\u7AEF\u52A0\u5BC6\u4F1A\u8BDD"));
bodyChildren.push(figure("08-settings.png", 480));
bodyChildren.push(caption("\u56FE 2-8  \u8BBE\u7F6E\u4E2D\u5FC3"));
bodyChildren.push(figure("09-ai-assistant.png", 480));
bodyChildren.push(caption("\u56FE 2-9  AI \u52A9\u624B\u5BF9\u8BDD\u9762\u677F"));

bodyChildren.push(h2("2.3  \u5F00\u53D1\u8FC7\u7A0B\u6587\u6863"));
bodyChildren.push(body("\u9879\u76EE\u5168\u7A0B\u4EE5\u6587\u6863\u5148\u884C\u7684\u65B9\u5F0F\u63A8\u8FDB\uFF0Cdocs \u76EE\u5F55\u4E0B\u5171\u6C89\u6DC0 27 \u7BC7\u8FC7\u7A0B\u6587\u6863\uFF0C\u8986\u76D6\u9700\u6C42\u5206\u6790\u3001\u524D\u540E\u7AEF\u8BBE\u8BA1\u89C4\u8303\u3001\u6570\u636E\u5E93\u8BBE\u8BA1\u3001\u52A0\u5BC6\u534F\u8BAE\u3001\u5B89\u5168\u5A01\u80C1\u6A21\u578B\u3001\u63A5\u53E3\u6587\u6863\u3001\u9636\u6BB5\u9A8C\u6536\u4E0E\u6F14\u793A\u6307\u5357\u7B49\u73AF\u8282\uFF1B\u5176\u4E2D docs/course-testing/ \u5B50\u76EE\u5F55\u4E3A\u672C\u8F6E\u8BFE\u7A0B\u6D4B\u8BD5\u5B9E\u8DF5\u4EA7\u51FA\uFF0C\u5305\u62EC\u8F6F\u4EF6\u9700\u6C42\u89C4\u683C\u8BF4\u660E\u4E66\u3001\u4E24\u6B21\u5B9E\u9A8C\u62A5\u544A\u3001\u5C0F\u7EC4\u5206\u5DE5\u8868\u7B49\u3002\u4E3B\u8981\u6587\u6863\u6E05\u5355\u5982\u8868 2-2 \u6240\u793A\u3002"));
bodyChildren.push(tableTitle("\u8868 2-2  \u5F00\u53D1\u8FC7\u7A0B\u6587\u6863\u6E05\u5355\uFF08docs/ \u76EE\u5F55\uFF09"));
bodyChildren.push(makeTable(["\u5206\u7C7B", "\u6587\u6863\u540D\u79F0"], docList, [22, 78]));
bodyChildren.push(body("\u4E0A\u8FF0\u6587\u6863\u5747\u968F\u4EE3\u7801\u4E00\u5E76\u7248\u672C\u5316\u7BA1\u7406\uFF0C\u4E0E\u5BF9\u5E94\u9636\u6BB5\u7684\u4EE3\u7801\u63D0\u4EA4\u4E92\u76F8\u5BF9\u5E94\uFF0C\u53EF\u8FFD\u6EAF\u6BCF\u4E00\u9879\u8BBE\u8BA1\u51B3\u7B56\u7684\u63D0\u51FA\u4E0E\u843D\u5730\u8FC7\u7A0B\u3002", { after: 160 }));

// 3 代码管理平台
bodyChildren.push(h1("3  \u4EE3\u7801\u7BA1\u7406\u5E73\u53F0"));
bodyChildren.push(body([
  bt("\u9879\u76EE\u4F7F\u7528 GitHub \u4F5C\u4E3A\u4EE3\u7801\u7BA1\u7406\u5E73\u53F0\uFF0C\u4ED3\u5E93\u5730\u5740\u4E3A\uFF1A"),
  new ExternalHyperlink({
    children: [new TextRun({ text: "https://github.com/HeanX/iChat_Pro", style: "Hyperlink", size: 24, font: { ascii: "Times New Roman", eastAsia: "SimSun" } })],
    link: "https://github.com/HeanX/iChat_Pro",
  }),
  bt("\u3002\u4ED3\u5E93\u4E3A\u516C\u5F00\u4ED3\u5E93\uFF0C\u4E3B\u5206\u652F\u4E3A main\uFF0C\u521B\u5EFA\u4E8E 2026 \u5E74 5 \u6708 29 \u65E5\uFF0C\u5168\u90E8\u4EE3\u7801\u3001\u6587\u6863\u4E0E Issue \u5747\u53EF\u5728\u7EBF\u8BBF\u95EE\u3002"),
]));
bodyChildren.push(body("\u5C0F\u7EC4\u534F\u4F5C\u6D41\u7A0B\u5B8C\u5168\u5728\u5E73\u53F0\u4E0A\u5C55\u5F00\uFF1A\u4EFB\u52A1\u4EE5 GitHub Issue \u5F62\u5F0F\u5EFA\u7ACB\u5E76\u7F16\u53F7\uFF0C\u6210\u5458\u8BA4\u9886\u540E\u4ECE main \u62C9\u51FA\u529F\u80FD\u5206\u652F\uFF08\u547D\u540D\u89C4\u8303\u4E3A feature/\u6210\u5458\u540D-\u4EFB\u52A1\u53F7\uFF09\uFF0C\u5F00\u53D1\u5B8C\u6210\u540E\u63D0\u4EA4 Pull Request\uFF0C\u7ECF\u975E\u4F5C\u8005\u6210\u5458\u8BC4\u5BA1\u901A\u8FC7\u540E\u5408\u5E76\u56DE main\u3002\u9879\u76EE\u6309\u9636\u6BB5\u63A8\u8FDB\uFF0C\u76EE\u524D\u5DF2\u5B8C\u6210\u4E09\u4E2A\u9636\u6BB5\u7684\u4EA4\u4ED8\u4E0E\u9A8C\u6536\uFF1A\u7B2C\u4E00\u671F\u5B8C\u6210\u6838\u5FC3\u804A\u5929\u4E0E\u52A0\u5BC6\u80FD\u529B\uFF0C\u7B2C\u4E8C\u671F\u5B8C\u6210\u8D26\u53F7\u4F53\u7CFB\u3001\u8BBE\u7F6E\u4E2D\u5FC3\u4E0E\u4F1A\u8BDD\u7BA1\u7406\u7B49\u5468\u8FB9\u80FD\u529B\uFF0C\u7B2C\u4E09\u671F\u5B8C\u6210 AI \u52A9\u624B\u4E0E\u6587\u6863\u3001UML \u7B49\u4EA4\u4ED8\u7269\u6574\u5408\uFF0C\u5404\u9636\u6BB5\u5747\u7559\u6709\u9A8C\u6536\u624B\u518C\u4E0E\u6F14\u793A\u6587\u6863\u3002"));
bodyChildren.push(body("\u4ED3\u5E93\u5F53\u524D\u4FDD\u6301 78 \u4E2A\u5F00\u653E Issue\uFF0C\u5BF9\u5E94\u4E0B\u4E00\u9636\u6BB5\uFF08\u7B2C\u56DB\u9636\u6BB5\uFF09\u7684\u4EFB\u52A1\u62C6\u89E3\uFF0C\u8BE6\u89C1\u672C\u62A5\u544A\u7B2C 5 \u7AE0\u3002", { after: 160 }));

// 4 小组分工与个人工作小结
bodyChildren.push(h1("4  \u5C0F\u7EC4\u5206\u5DE5\u4E0E\u4E2A\u4EBA\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(h2("4.1  \u5206\u5DE5\u673A\u5236"));
bodyChildren.push(body("\u5C0F\u7EC4\u91C7\u7528 Issue \u9A71\u52A8\u7684\u5206\u5DE5\u65B9\u5F0F\uFF1A\u6BCF\u4E2A\u4EFB\u52A1\u5EFA\u7ACB\u4E00\u4E2A Issue\uFF0C\u660E\u786E\u76EE\u6807\u3001\u4EA4\u4ED8\u7269\u4E0E\u9A8C\u6536\u6807\u51C6\uFF1B\u6210\u5458\u5728 Issue \u4E2D\u8BA4\u9886\u540E\u72EC\u7ACB\u5F00\u53D1\uFF0C\u4EE5 Pull Request \u63D0\u4EA4\u6210\u679C\uFF0C\u7531\u975E\u4F5C\u8005\u6210\u5458\u8BC4\u5BA1\u540E\u5408\u5E76\u3002\u5F00\u53D1\u4E0E\u9A8C\u6536\u89D2\u8272\u5206\u79BB\uFF0C\u6D89\u53CA\u5B89\u5168\u7684\u5173\u952E\u6539\u52A8\u81F3\u5C11\u7531\u4E24\u4EBA\u590D\u6838\uFF1B\u6BCF\u4E2A\u9636\u6BB5\u7ED3\u675F\u65F6\u8FDB\u884C\u4E00\u6B21\u96C6\u4E2D\u9A8C\u6536\u5E76\u8F93\u51FA\u9A8C\u6536\u6587\u6863\u3002\u4EE3\u7801\u63D0\u4EA4\u8BB0\u5F55\u4E0E Issue\u3001PR \u4E92\u76F8\u5173\u8054\uFF0C\u6BCF\u4F4D\u6210\u5458\u7684\u5DE5\u4F5C\u91CF\u4E0E\u8D21\u732E\u65B9\u5411\u5747\u53EF\u8FFD\u6EAF\u3002"));
bodyChildren.push(h2("4.2  \u6210\u5458\u5206\u5DE5\u603B\u89C8"));
bodyChildren.push(body("\u5C0F\u7EC4\u5171 4 \u540D\u6210\u5458\uFF0C\u5747\u53C2\u4E0E\u7F16\u7801\u4E0E\u6587\u6863\u5DE5\u4F5C\u3002\u4E0B\u8868\u6C47\u603B\u5404\u6210\u5458\u7684\u89D2\u8272\u5B9A\u4F4D\u4E0E\u4E3B\u8981\u8D1F\u8D23\u5185\u5BB9\uFF0C\u63D0\u4EA4\u6570\u4E3A\u4E3B\u5206\u652F\u4E0A\u7684\u63D0\u4EA4\u8BB0\u5F55\u6570\uFF08\u5408\u8BA1 131 \u6B21\uFF09\u3002"));
bodyChildren.push(tableTitle("\u8868 4-1  \u5C0F\u7EC4\u6210\u5458\u5206\u5DE5\u603B\u89C8"));
bodyChildren.push(makeTable(["\u6210\u5458", "\u5B66\u53F7", "\u89D2\u8272\u5B9A\u4F4D", "\u63D0\u4EA4\u6570", "\u4E3B\u8981\u8D1F\u8D23\u5185\u5BB9"], members, [19, 18, 17, 10, 36]));

bodyChildren.push(h2("4.3  \u4E2A\u4EBA\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(h3("4.3.1  \u53F6\u4F1F\u667A\uFF08HeanX\uFF0C\u7EC4\u957F\uFF09\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(body("\u4F5C\u4E3A\u7EC4\u957F\u8D1F\u8D23\u9879\u76EE\u603B\u4F53\u7EDF\u7B79\u3001\u8FED\u4EE3\u8282\u594F\u63A7\u5236\u4E0E Pull Request \u7684\u8BC4\u5BA1\u5408\u5E76\uFF0C\u540C\u65F6\u627F\u62C5\u524D\u7AEF\u4E0E\u4EA4\u4ED8\u6587\u6863\u7684\u6838\u5FC3\u5F00\u53D1\u3002\u524D\u7AEF\u65B9\u9762\u5B8C\u6210\u5DE5\u4F5C\u53F0\u5E03\u5C40\u91CD\u6784\u3001\u8054\u7CFB\u4EBA\u4E0E\u7FA4\u7EC4\u72EC\u7ACB\u9875\u9762\u5411\u53F3\u4FA7\u9762\u677F\u5DE5\u4F5C\u533A\u7684\u6574\u5408\u4EE5\u53CA\u754C\u9762\u56FD\u9645\u5316\uFF08i18n\uFF09\u652F\u6301\uFF1B\u540E\u7AEF\u65B9\u9762\u5B9E\u73B0\u4F1A\u8BDD\u7BA1\u7406\u80FD\u529B\uFF08\u7F6E\u9876\u3001\u514D\u6253\u6270\u3001\u5F52\u6863\u7B49\uFF0CT19\u2013T22\uFF09\u4E0E\u6D88\u606F\u64CD\u4F5C\u3001\u6D88\u606F\u53EF\u9760\u6027\u76F8\u5173\u63A5\u53E3\uFF08T27\u3001T31\u2013T34\u3001T37\u2013T38\u3001T40\uFF09\uFF0C\u5E76\u5B9E\u73B0\u652F\u6301\u7528\u6237\u540D\u3001\u6635\u79F0\u4E0E\u7528\u6237 ID \u7684\u8054\u7CFB\u4EBA\u641C\u7D22\uFF1B\u4EA4\u4ED8\u65B9\u9762\u72EC\u7ACB\u91CD\u5199\u5168\u5957 UML \u4E0E\u67B6\u6784\u56FE\u5E76\u4FEE\u590D Mermaid \u6E32\u67D3\u95EE\u9898\uFF0C\u5B8C\u6210\u7B2C\u4E09\u9636\u6BB5\u56DE\u5F52\u6D4B\u8BD5\u4E0E\u6F14\u793A\u6587\u6863\uFF08T01/T07/T08\uFF09\uFF1B\u6B64\u5916\u642D\u5EFA\u7B2C\u4E09\u9636\u6BB5 AI \u52A9\u624B\u57FA\u7840\u67B6\u6784\u4E0E\u72B6\u6001\u6A21\u5F0F\uFF08\u63A5\u5165 qwen-plus \u6A21\u578B\uFF09\uFF0C\u5B8C\u6210\u4F1A\u8BDD\u7BA1\u7406\u3001\u8F6C\u53D1\u6821\u9A8C\u3001\u5185\u5BB9\u5B89\u5168\u7B56\u7565\uFF08CSP\uFF09\u4E0E\u767B\u5F55\u6D41\u7A0B\u7B49\u591A\u8F6E\u5B89\u5168\u52A0\u56FA\u3002\u7D2F\u8BA1\u63D0\u4EA4 80 \u6B21\u3002"));
bodyChildren.push(h3("4.3.2  \u8BB8\u6822\u5883\uFF08AEther61\uFF09\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(body("\u4E3B\u8981\u8D1F\u8D23\u804A\u5929\u6570\u636E\u6A21\u578B\u4E0E\u8D26\u53F7\u9690\u79C1\u65B9\u5411\u7684\u540E\u7AEF\u5F00\u53D1\u3002\u65E9\u671F\u5B8C\u6210\u804A\u5929\u6570\u636E\u6A21\u578B\uFF08chat models\uFF09\u7684\u8BBE\u8BA1\u4E0E\u5B9E\u73B0\u3001\u7FA4\u7EC4\u6A21\u578B\u6574\u5408\uFF08T22\uFF09\u4E0E\u7FA4\u7EC4\u6743\u9650\u4FEE\u590D\uFF08T23\uFF09\uFF1B\u968F\u540E\u4F9D\u6B21\u5B9E\u73B0\u9690\u79C1\u8BBE\u7F6E\uFF08T25\uFF09\u3001\u62C9\u9ED1\u7528\u6237\u7BA1\u7406\uFF08T26\uFF09\u3001Passkey \u5B57\u6BB5\uFF08T28\uFF09\u3001\u7528\u6237\u8D44\u6599\u6269\u5C55\uFF08T29\uFF09\u3001\u4E8C\u7EF4\u7801\u540D\u7247\uFF08T30\uFF09\u3001\u591A\u8D26\u53F7\u4E0A\u4E0B\u6587\uFF08T35\uFF09\u3001\u4F1A\u8BDD\u7BA1\u7406 API\uFF08T36\uFF09\u4E0E\u8D44\u6599\u540C\u6B65\u4E8B\u4EF6\uFF08T39\uFF09\u7B49\u8D26\u53F7\u4E0E\u9690\u79C1\u540E\u7AEF\u80FD\u529B\uFF0C\u5E76\u5728 WebSocket \u5C42\u843D\u5B9E\u62C9\u9ED1\u3001\u514D\u6253\u6270\u7684\u5F3A\u5236\u6267\u884C\u4E0E\u591A\u8BBE\u5907\u5728\u7EBF\u72B6\u6001\u7EF4\u62A4\uFF1B\u7B2C\u4E09\u9636\u6BB5\u5B9E\u73B0 AI \u52A9\u624B\u72B6\u6001\u7AEF\u70B9\u4E0E\u5206\u6A21\u5F0F\u7CFB\u7EDF\u63D0\u793A\u8BCD\uFF08P3 T04/T05\uFF09\uFF0C\u540C\u65F6\u53C2\u4E0E\u7B2C\u4E8C\u9636\u6BB5\u540E\u7AEF\u4EFB\u52A1\u4E0E\u91CD\u590D\u89C6\u56FE\u6E05\u7406\u3002\u7D2F\u8BA1\u63D0\u4EA4 37 \u6B21\u3002"));
bodyChildren.push(h3("4.3.3  \u5E9E\u5609\u94ED\uFF08pislan\uFF09\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(body("\u4E3B\u8981\u8D1F\u8D23\u8BBE\u7F6E\u4E2D\u5FC3\u7684\u524D\u7AEF\u5B9E\u73B0\uFF1A\u5B8C\u6210\u4FA7\u8FB9\u680F\u591A\u89C6\u56FE\u5BFC\u822A\u91CD\u6784\uFF08T01\uFF09\u3001\u8BBE\u7F6E\u4E2D\u5FC3\u4E3B\u9875\uFF08T02\uFF09\u3001\u4E2A\u4EBA\u8D44\u6599\u7F16\u8F91\u4E0E\u4E8C\u7EF4\u7801\u5F39\u7A97\uFF08T03\uFF09\u3001\u901A\u77E5\u8BBE\u7F6E\uFF08T04\uFF09\u3001\u6570\u636E\u4E0E\u5B58\u50A8\u8BBE\u7F6E\uFF08T05\uFF09\u3001\u9690\u79C1\u4E0E\u5B89\u5168\u8BBE\u7F6E\uFF08T06\uFF09\u516D\u4E2A\u9875\u9762\uFF0C\u5E76\u7F16\u5199\u7B2C\u4E8C\u9636\u6BB5\u4EBA\u5DE5\u9A8C\u6536\u6E05\u5355\uFF08T18\uFF09\uFF0C\u4E3A\u9636\u6BB5\u9A8C\u6536\u63D0\u4F9B\u4E86\u53EF\u6267\u884C\u7684\u68C0\u67E5\u6E05\u5355\u3002\u7D2F\u8BA1\u63D0\u4EA4 7 \u6B21\u3002"));
bodyChildren.push(h3("4.3.4  \u6797\u5F18\uFF08ketter1024\uFF09\u5DE5\u4F5C\u5C0F\u7ED3"));
bodyChildren.push(body("\u4E3B\u8981\u8D1F\u8D23 accounts \u5E94\u7528\u7684\u5B9E\u73B0\uFF0C\u8986\u76D6\u7528\u6237\u8D44\u6599\u3001\u8BA4\u8BC1\u3001\u8054\u7CFB\u4EBA\u4E0E\u5BA2\u6237\u7AEF\u5BC6\u94A5\u7B49\u6A21\u5757\uFF1B\u540C\u65F6\u7EF4\u62A4\u5168\u5C40\u914D\u7F6E\u4E0E\u4F9D\u8D56\u6E05\u5355\uFF08settings\u3001urls\u3001requirements\uFF09\uFF0C\u5E76\u53C2\u4E0E\u591A\u4E2A\u529F\u80FD\u5206\u652F\u7684\u5408\u5E76\u7BA1\u7406\u3002\u7D2F\u8BA1\u63D0\u4EA4 7 \u6B21\u3002", { after: 160 }));

// 5 小组后续要解决的问题
bodyChildren.push(h1("5  \u5C0F\u7EC4\u540E\u7EED\u8981\u89E3\u51B3\u7684\u95EE\u9898"));
bodyChildren.push(body("\u9879\u76EE\u4E0B\u4E00\u9636\u6BB5\uFF08\u7B2C\u56DB\u9636\u6BB5\uFF09\u7684\u5168\u90E8\u5DE5\u4F5C\u5DF2\u5728 GitHub \u4E0A\u5B8C\u6210\u4EFB\u52A1\u62C6\u89E3\uFF0C\u5171 78 \u4E2A\u5F00\u653E Issue\uFF08\u7F16\u53F7 #128\u2013#205\uFF09\uFF0C\u5206\u4E3A 54 \u9879\u5DE5\u7A0B\u4EFB\u52A1\uFF08P4 T01\u2013T54\uFF09\u4E0E 24 \u9879\u6D4B\u8BD5\u4EFB\u52A1\uFF08P4 Test T01\u2013T24\uFF09\u3002\u6240\u6709 Issue \u5EFA\u7ACB\u65F6\u5747\u672A\u6307\u5B9A\u8D1F\u8D23\u4EBA\uFF0C\u5C06\u7531\u5C0F\u7EC4\u7EDF\u4E00\u5206\u5DE5\u540E\u8BA4\u9886\u5B9E\u65BD\u3002\u5404\u5DE5\u4F5C\u5305\u4E0E Issue \u5206\u5E03\u5982\u8868 5-1 \u6240\u793A\u3002"));
bodyChildren.push(tableTitle("\u8868 5-1  \u7B2C\u56DB\u9636\u6BB5\u5DE5\u4F5C\u5305\u4E0E Issue \u5206\u5E03"));
bodyChildren.push(makeTable(["\u5DE5\u4F5C\u5305", "\u4EFB\u52A1\u7F16\u53F7", "Issue \u7F16\u53F7", "\u6570\u91CF", "\u4E3B\u8981\u4EA4\u4ED8\u7269"], workPackages, [24, 15, 14, 8, 39]));
bodyChildren.push(h2("5.1  \u91CD\u70B9\u95EE\u9898\u8BF4\u660E"));
bodyChildren.push(body("\u7B2C\u4E00\uFF0C\u4E91\u7AEF\u751F\u4EA7\u90E8\u7F72\uFF08#131\u2013#140\uFF09\u3002\u5F53\u524D\u7CFB\u7EDF\u4EC5\u5728\u672C\u5730\u73AF\u5883\u8FD0\u884C\uFF0C\u9700\u5B8C\u6210\u4E91\u670D\u52A1\u5668\u4E0E\u57DF\u540D\u51C6\u5907\u3001Nginx \u4E0E HTTPS/WSS \u914D\u7F6E\u3001Django ASGI \u670D\u52A1\u90E8\u7F72\u3001PostgreSQL \u4E0E Redis Channel Layer \u63A5\u5165\u3001\u6301\u4E45\u5316\u6587\u4EF6\u5B58\u50A8\u3001\u5065\u5EB7\u68C0\u67E5\u4E0E\u5907\u4EFD\u56DE\u6EDA\u7B49\u5DE5\u4F5C\uFF0C\u4F7F\u7CFB\u7EDF\u5177\u5907\u5BF9\u5916\u670D\u52A1\u80FD\u529B\u3002"));
bodyChildren.push(body("\u7B2C\u4E8C\uFF0C\u8DE8\u5E73\u53F0\u5BA2\u6237\u7AEF\uFF08#141\u2013#157\uFF09\u3002Windows \u7AEF\u9700\u5B8C\u6210 Electron \u5B89\u88C5\u5305\u6253\u5305\u3001\u7A97\u53E3\u4E0E\u7CFB\u7EDF\u6258\u76D8\u3001\u7CFB\u7EDF\u901A\u77E5\u3001\u6587\u4EF6\u9009\u62E9\u4E0E\u5B89\u5168\u5B58\u50A8\u7B49\u9002\u914D\uFF1BAndroid \u7AEF\u9700\u57FA\u4E8E Capacitor \u786E\u5B9A\u6280\u672F\u65B9\u6848\uFF0C\u5B8C\u6210\u79FB\u52A8\u7AEF\u54CD\u5E94\u5F0F\u9002\u914D\u3001\u524D\u540E\u53F0\u901A\u77E5\u3001\u8FD4\u56DE\u952E\u4E0E\u751F\u547D\u5468\u671F\u9002\u914D\u3001\u6587\u4EF6\u9009\u62E9\u4E0E\u4FDD\u5B58\uFF0C\u6700\u7EC8\u4EA7\u51FA\u53EF\u5B89\u88C5\u7684 APK/AAB\u3002"));
bodyChildren.push(body("\u7B2C\u4E09\uFF0C\u6D88\u606F\u53EF\u9760\u6027\u4E0E E2EE \u5B89\u5168\u589E\u5F3A\uFF08#158\u2013#170\uFF09\u3002\u8BA1\u5212\u7EDF\u4E00\u4E09\u7AEF\u534F\u8BAE\u7248\u672C\u4E0E\u9519\u8BEF\u7801\uFF0C\u5B8C\u5584\u6D88\u606F\u53D1\u9001\u72B6\u6001\u673A\u3001\u6D88\u606F\u552F\u4E00 ID \u4E0E\u5E42\u7B49\u6027\u3001\u79BB\u7EBF\u6D88\u606F\u8865\u53D6\u4E0E\u5F31\u7F51\u91CD\u8FDE\uFF1B\u5B89\u5168\u65B9\u9762\u5C06\u5B9E\u73B0\u8BBE\u5907\u8EAB\u4EFD\u6A21\u578B\u3001\u8BBE\u5907\u5217\u8868\u4E0E\u64A4\u9500\u3001\u591A\u8BBE\u5907\u65B0\u6D88\u606F\u5BC6\u94A5\u5206\u53D1\u4E0E\u5386\u53F2\u5BC6\u94A5\u8FC1\u79FB\uFF0C\u5E76\u7F16\u5236 E2EE \u5A01\u80C1\u6A21\u578B\u4E0E\u6570\u636E\u6D41\u56FE\u3002"));
bodyChildren.push(body("\u7B2C\u56DB\uFF0C\u5DE5\u7A0B\u5316\u4E0E\u4EA7\u4E1A\u7EA7\u6D4B\u8BD5\uFF08#171\u2013#205\uFF09\u3002\u5DE5\u7A0B\u5316\u65B9\u9762\u63A5\u5165\u9759\u6001\u68C0\u67E5\u4E0E\u683C\u5F0F\u95E8\u7981\u3001Django \u81EA\u52A8\u5316\u6D4B\u8BD5\u4E0E\u8FC1\u79FB\u68C0\u67E5\u3001\u8986\u76D6\u7387\u95E8\u7981\u3001\u4F9D\u8D56\u4E0E\u79D8\u5BC6\u626B\u63CF\uFF0C\u5E76\u5EFA\u7ACB\u6807\u51C6\u5316\u6784\u5EFA\u4E0E\u53D1\u5E03\u56DE\u6EDA\u6D41\u7A0B\uFF1B\u6D4B\u8BD5\u65B9\u9762\u4F9D\u636E\u8F6F\u4EF6\u9700\u6C42\u89C4\u683C\u8BF4\u660E\u4E66\u7F16\u5199\u603B\u4F53\u6D4B\u8BD5\u8BA1\u5212\u4E0E\u9700\u6C42\u8FFD\u8E2A\u77E9\u9635\uFF0C\u5F00\u5C55\u9ED1\u76D2\u4E0E\u767D\u76D2\u6D4B\u8BD5\u8BBE\u8BA1\u3001\u96C6\u6210\u6D4B\u8BD5\u3001\u517C\u5BB9\u6027\u6D4B\u8BD5\u3001\u5B89\u5168\u6D4B\u8BD5\u3001\u6027\u80FD\u7A33\u5B9A\u6027\u4E0E\u5F31\u7F51\u6D4B\u8BD5\uFF0C\u5E76\u5EFA\u7ACB\u7F3A\u9677\u7BA1\u7406\u4E0E\u56DE\u5F52\u6D41\u7A0B\uFF0C\u6700\u7EC8\u8F93\u51FA\u6D4B\u8BD5\u603B\u7ED3\u4E0E\u9A8C\u6536\u7ED3\u8BBA\u3002"));
bodyChildren.push(h2("5.2  \u4EFB\u52A1\u8BA4\u9886\u4E0E\u534F\u4F5C\u65B9\u5F0F"));
bodyChildren.push(body("\u540E\u7EED\u4EFB\u52A1\u5C06\u5EF6\u7EED\u65E2\u6709\u534F\u4F5C\u673A\u5236\uFF1A\u6210\u5458\u5728 GitHub Issue \u4E2D\u8BBE\u7F6E\u8D1F\u8D23\u4EBA\uFF08Assignee\uFF09\u5E76\u8BC4\u8BBA\u9884\u8BA1\u5B8C\u6210\u65F6\u95F4\u4E0E\u4F9D\u8D56\uFF1B\u6BCF\u4E2A Issue \u6307\u5B9A\u4E00\u540D\u4E3B\u8D1F\u8D23\u4EBA\u548C\u81F3\u5C11\u4E00\u540D\u9A8C\u6536\u4EBA\uFF0C\u5F00\u53D1\u4EBA\u5458\u4E0D\u5F97\u5355\u72EC\u9A8C\u6536\u81EA\u5DF1\u7684\u5173\u952E\u529F\u80FD\uFF1B\u6D89\u53CA\u5B89\u5168\u3001E2EE \u4E0E\u53D1\u5E03\u64CD\u4F5C\u7684\u6539\u52A8\u81F3\u5C11\u7531\u4E24\u4EBA\u590D\u6838\uFF1B\u5C0F\u7EC4\u6BCF\u5468\u6838\u5BF9\u4E00\u6B21 Issue\u3001PR\u3001\u7F3A\u9677\u4E0E\u6D4B\u8BD5\u8BC1\u636E\uFF0C\u786E\u4FDD\u8FDB\u5EA6\u53EF\u63A7\u3001\u8D28\u91CF\u53EF\u9A8C\u3002\u5177\u4F53\u4EFB\u52A1\u6E05\u5355\u89C1\u4ED3\u5E93 Issues \u9875\uFF1Ahttps://github.com/HeanX/iChat_Pro/issues\u3002", { after: 160 }));

// 结束语
bodyChildren.push(h1("\u7ED3\u675F\u8BED"));
bodyChildren.push(body("\u76EE\u524D\uFF0CiChat Pro \u7684\u6838\u5FC3\u529F\u80FD\u5F00\u53D1\u4E0E\u8FC7\u7A0B\u6587\u6863\u4EA4\u4ED8\u5747\u5DF2\u5B8C\u6210\uFF0C\u4EE3\u7801\u6258\u7BA1\u4E8E GitHub \u5E76\u4FDD\u6301\u6301\u7EED\u6F14\u8FDB\u3002\u5C0F\u7EC4\u5C06\u5728\u65E2\u6709\u57FA\u7840\u4E0A\uFF0C\u6309\u7B2C\u56DB\u9636\u6BB5\u89C4\u5212\u63A8\u8FDB\u4E91\u7AEF\u90E8\u7F72\u3001\u8DE8\u5E73\u53F0\u5BA2\u6237\u7AEF\u3001\u5B89\u5168\u589E\u5F3A\u4E0E\u4EA7\u4E1A\u7EA7\u6D4B\u8BD5\u5DE5\u4F5C\uFF0C\u9010\u6B65\u628A\u9879\u76EE\u4ECE\u8BFE\u7A0B\u6F14\u793A\u4EA7\u54C1\u63A8\u5411\u53EF\u5BF9\u5916\u670D\u52A1\u7684\u5B8C\u6574\u4EA7\u54C1\u3002", { after: 0 }));

// ─── document assembly ───
const pgSize = { width: 11906, height: 16838 };
const pgMargin = { top: 1440, bottom: 1440, left: 1701, right: 1417 };

function pageNumFooter() {
  return new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [new TextRun({ children: [PageNumber.CURRENT], size: 18, color: "808080", font: { ascii: "Times New Roman" } })],
    })],
  });
}
function docHeader() {
  return new Header({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: "C8DDE2", space: 4 } },
      children: [new TextRun({ text: "iChat Pro \u9879\u76EE\u8BFE\u7A0B\u4EA4\u4ED8\u603B\u7ED3\u62A5\u544A", size: 18, color: "808080", font: { ascii: "Times New Roman", eastAsia: "SimSun" } })],
    })],
  });
}

const doc = new Document({
  creator: "iChat Pro \u9879\u76EE\u5C0F\u7EC4",
  title: "iChat Pro \u9879\u76EE\u8BFE\u7A0B\u4EA4\u4ED8\u603B\u7ED3\u62A5\u544A",
  styles: {
    default: {
      document: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimSun" }, size: 24, color: "000000" },
        paragraph: { spacing: { line: 312 } },
      },
      heading1: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimHei" }, size: 32, bold: true, color: P.primary },
        paragraph: { spacing: { before: 360, after: 160, line: 380 }, outlineLevel: 0 },
      },
      heading2: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimHei" }, size: 28, bold: true, color: P.primary },
        paragraph: { spacing: { before: 280, after: 120, line: 340 }, outlineLevel: 1 },
      },
      heading3: {
        run: { font: { ascii: "Times New Roman", eastAsia: "SimHei" }, size: 24, bold: true, color: P.primary },
        paragraph: { spacing: { before: 220, after: 100, line: 312 }, outlineLevel: 2 },
      },
    },
  },
  sections: [
    // Section 1: cover — no page number, no footer
    {
      properties: {
        page: { size: pgSize, margin: { top: 0, bottom: 0, left: 0, right: 0 } },
      },
      children: buildCoverR1({
        title: "iChat Pro \u9879\u76EE\u8BFE\u7A0B\u4EA4\u4ED8\u603B\u7ED3\u62A5\u544A",
        subtitle: "\u57FA\u4E8E\u7AEF\u5230\u7AEF\u52A0\u5BC6\uFF08E2EE\uFF09\u7684\u5B89\u5168\u5373\u65F6\u901A\u4FE1\u7CFB\u7EDF",
        englishLabel: "PROJECT DELIVERY REPORT",
        metaLines: [
          "\u8BFE\u7A0B\u540D\u79F0\uFF1A\u3010\u8BF7\u586B\u5199\u3011",
          "\u6307\u5BFC\u6559\u5E08\uFF1A\u3010\u8BF7\u586B\u5199\u3011",
          "\u5C0F\u7EC4\u6210\u5458\uFF1A\u53F6\u4F1F\u667A\u3001\u8BB8\u6822\u5883\u3001\u5E9E\u5609\u94ED\u3001\u6797\u5F18",
          "GitHub \u8D26\u53F7\uFF1AHeanX\u3001AEther61\u3001pislan\u3001ketter1024",
          "\u4EE3\u7801\u4ED3\u5E93\uFF1Agithub.com/HeanX/iChat_Pro",
          "\u62A5\u544A\u65E5\u671F\uFF1A2026 \u5E74 9 \u6708 30 \u65E5",
        ],
        footerLeft: "iChat Pro \u9879\u76EE\u5C0F\u7EC4",
        footerRight: "2026-09",
        bg: P.bg,
        palette: { ...P.cover, accent: P.accent },
      }),
    },
    // Section 2: TOC — roman numerals
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: pgSize, margin: pgMargin, pageNumbers: { start: 1, formatType: NumberFormat.UPPER_ROMAN } },
      },
      footers: { default: pageNumFooter() },
      children: [
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { before: 240, after: 240 },
          children: [new TextRun({ text: "\u76EE  \u5F55", bold: true, size: 32, font: { eastAsia: "SimHei", ascii: "Times New Roman" }, color: P.primary })],
        }),
        new TableOfContents("Table of Contents", { hyperlink: true, headingStyleRange: "1-2" }),
        new Paragraph({
          spacing: { before: 200 },
          children: [new TextRun({
            text: "\u6CE8\uFF1A\u672C\u76EE\u5F55\u7531\u57DF\u4EE3\u7801\u751F\u6210\u3002\u5982\u5BF9\u6587\u6863\u6709\u7F16\u8F91\uFF0C\u8BF7\u5728\u76EE\u5F55\u4E0A\u53F3\u952E\u9009\u62E9\u201C\u66F4\u65B0\u57DF\u201D\u4EE5\u5237\u65B0\u9875\u7801\u3002",
            italics: true, size: 18, color: "888888", font: { ascii: "Times New Roman", eastAsia: "SimSun" },
          })],
        }),
        new Paragraph({ children: [new PageBreak()] }),
      ],
    },
    // Section 3: body — arabic from 1
    {
      properties: {
        type: SectionType.NEXT_PAGE,
        page: { size: pgSize, margin: pgMargin, pageNumbers: { start: 1, formatType: NumberFormat.DECIMAL } },
      },
      headers: { default: docHeader() },
      footers: { default: pageNumFooter() },
      children: bodyChildren,
    },
  ],
});

const OUT = path.join(__dirname, "..", process.argv[2] || "iChat Pro \u9879\u76EE\u8BFE\u7A0B\u4EA4\u4ED8\u603B\u7ED3\u62A5\u544A.docx");
Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("written:", OUT, buf.length, "bytes");
});
