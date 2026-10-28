import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import { Spin } from "antd";
import { api } from "../api";

/**
 * 需求文档预览：md 渲染 / txt 原文 / docx 前端转 html（mammoth）。
 * docx 预览是尽力而为——复杂排版以下载后的原文件为准。
 */

export interface DocMeta {
  id: number;
  filename: string;
  version?: number;
}

export default function DocPreview({ reqId, doc }: { reqId: number; doc: DocMeta }) {
  const [md, setMd] = useState<string | null>(null);
  const [html, setHtml] = useState<string | null>(null);
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let alive = true;
    setMd(null);
    setHtml(null);
    setText(null);
    setErr("");
    (async () => {
      try {
        if (/\.(md|markdown)$/i.test(doc.filename)) {
          const c = await api<{ content: string }>(`/api/requirements/${reqId}/docs/${doc.id}`);
          if (alive) setMd(c.content || "（空文件）");
        } else if (/\.txt$/i.test(doc.filename)) {
          const c = await api<{ content: string }>(`/api/requirements/${reqId}/docs/${doc.id}`);
          if (alive) setText(c.content || "（空文件）");
        } else {
          // docx：取原始字节 → mammoth 转 html
          const token = localStorage.getItem("vega_token") || "";
          const r = await fetch(`/api/requirements/${reqId}/docs/${doc.id}/download`, {
            headers: { Authorization: `Bearer ${token}` },
          });
          if (!r.ok) throw new Error(`下载失败（${r.status}）`);
          const buf = await r.arrayBuffer();
          const mammoth = await import("mammoth/mammoth.browser");
          const out = await mammoth.convertToHtml({ arrayBuffer: buf });
          if (alive) setHtml(out.value || "（docx 无文本内容）");
        }
      } catch (e: any) {
        if (alive) setErr(e.message || "预览失败");
      }
    })();
    return () => {
      alive = false;
    };
  }, [reqId, doc.id, doc.filename]);

  if (err) return <div className="md-body">{err}</div>;
  if (md === null && html === null && text === null) return <Spin style={{ padding: 24, display: "block" }} />;
  if (md !== null) return (
    <div className="md-body">
      <Markdown>{md}</Markdown>
    </div>
  );
  if (html !== null) return <div className="md-body docx-body" dangerouslySetInnerHTML={{ __html: html }} />;
  return (
    <pre className="txt-body">{text}</pre>
  );
}
