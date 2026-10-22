import { Fragment, ReactNode } from "react";

// A tiny, safe Markdown renderer for chat answers: paragraphs, headings, bullet and numbered
// lists, tables, **bold**, *italic* and `code`. It builds React elements (never raw HTML),
// so an answer can't inject markup into the page.

function inline(text: string, keyPrefix: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const k = `${keyPrefix}-${i++}`;
    if (tok.startsWith("**")) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>);
    else if (tok.startsWith("`")) out.push(<code key={k}>{tok.slice(1, -1)}</code>);
    else out.push(<em key={k}>{tok.slice(1, -1)}</em>);
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const isTableRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isDivider = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
const cells = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

export default function Markdown({ text }: { text: string }) {
  const lines = String(text ?? "").replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    // table: header row + divider row + body rows
    if (isTableRow(line) && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const head = cells(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && isTableRow(lines[i])) rows.push(cells(lines[i++]));
      const k = key++;
      blocks.push(
        <div className="md-table-wrap" key={k}>
          <table className="md-table">
            <thead>
              <tr>{head.map((h, j) => <th key={j}>{inline(h, `h${k}-${j}`)}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inline(c, `c${k}-${ri}-${ci}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    // heading
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const k = key++;
      blocks.push(<div className="md-h" key={k}>{inline(h[2], `t${k}`)}</div>);
      i++;
      continue;
    }
    // bullet / numbered list
    if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
        i++;
      }
      const k = key++;
      const List = ordered ? "ol" : "ul";
      blocks.push(
        <List className="md-list" key={k}>
          {items.map((it, j) => <li key={j}>{inline(it, `l${k}-${j}`)}</li>)}
        </List>,
      );
      continue;
    }
    // paragraph: consecutive plain lines
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !isTableRow(lines[i]) && !/^(#{1,4})\s+/.test(lines[i]) &&
           !/^\s*([-*•]|\d+[.)])\s+/.test(lines[i])) {
      para.push(lines[i++]);
    }
    const k = key++;
    blocks.push(
      <p className="md-p" key={k}>
        {para.map((p, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {inline(p, `p${k}-${j}`)}
          </Fragment>
        ))}
      </p>,
    );
  }
  return <div className="md">{blocks}</div>;
}
