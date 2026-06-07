import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { useBackend } from "../backend";
import { DEMO_DOCS } from "../mock/dataset";
import { Icon } from "../components/Icon";
import { clock } from "../util";
import { fadeUp, inView, item, pressable, staggerParent } from "../motion";

const ACCEPT = ".pdf,.docx,.txt,.md,.html,.csv,.pptx,.xlsx";

export function KnowledgeView() {
  const { call } = useBackend();
  const { data, loading, error, demo, reload } = useResource(
    () => api.listDocuments().then((r) => r.documents),
    DEMO_DOCS,
  );
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);
  // Filenames currently being uploaded (before they appear in the list).
  const [uploading, setUploading] = useState<string[]>([]);

  // ── Async ingestion: while any doc is "processing", poll the list so the row
  //    flips processing → ready without a manual refresh. ──────────────────────
  const hasProcessing = !!data?.some((d) => d.status === "processing");
  useEffect(() => {
    if (demo || !hasProcessing) return;
    const t = setTimeout(reload, 3000);
    return () => clearTimeout(t);
  }, [demo, hasProcessing, data, reload]);

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const valid = files.filter((f) => f.size > 0);
      if (!valid.length) return;
      setUploading((u) => [...u, ...valid.map((f) => f.name)]);
      for (const file of valid) {
        await call("Upload document", () => api.uploadDocument(file, file.name), {
          endpoint: "POST /documents",
          success: `Ingesting ${file.name}…`,
        });
        setUploading((u) => u.filter((n) => n !== file.name));
        reload();
      }
    },
    [call, reload],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragActive(false);
      const files = Array.from(e.dataTransfer.files ?? []);
      if (files.length) void uploadFiles(files);
    },
    [uploadFiles],
  );

  const onDelete = async (id: string, title: string) => {
    await call("Delete document", () => api.deleteDocument(id), {
      endpoint: `DELETE /documents/${id}`,
      success: `Removed ${title}`,
    });
    reload();
  };

  return (
    <div
      className={`section-page${dragActive ? " drag-active" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!demo) setDragActive(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        if (e.currentTarget === e.target) setDragActive(false);
      }}
      onDrop={onDrop}
    >
      <motion.div className="section-page-head" variants={fadeUp} initial="hidden" animate="show">
        <div>
          <h1 className="page-title">Knowledge</h1>
          <p className="page-sub">
            Documents Relay retrieves from. Drag &amp; drop a file anywhere, or upload — it's
            parsed (Unsiloed), chunked, and indexed (Moss) automatically.
          </p>
        </div>
        <motion.button className="btn-primary" onClick={() => fileRef.current?.click()} {...pressable}>
          <Icon name="upload_file" size={16} />
          Upload document
        </motion.button>
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          hidden
          multiple
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) void uploadFiles(files);
            e.target.value = "";
          }}
        />
      </motion.div>

      {demo && <DemoBanner endpoint="GET /documents" />}
      {loading && <div className="page-empty">Loading documents…</div>}
      {error && <div className="page-empty error">Couldn’t load documents — {error}</div>}

      {(data || uploading.length > 0) && (
        <motion.div
          className="card-surface table"
          variants={staggerParent(0.05)}
          initial="hidden"
          whileInView="show"
          viewport={inView}
        >
          <div className="table-head">
            <span>Title</span>
            <span>Status</span>
            <span>Chunks</span>
            <span>Created</span>
            <span></span>
          </div>

          {/* Files uploading (optimistic rows, before the backend list reflects them). */}
          {uploading.map((name) => (
            <div className="table-row" key={`up-${name}`}>
              <span className="td-title">
                <Icon name="description" size={18} />
                {name}
              </span>
              <span>
                <span className="status-dot processing" />
                uploading…
              </span>
              <span className="mono">—</span>
              <span className="mono">—</span>
              <span />
            </div>
          ))}

          {data?.map((d) => (
            <motion.div className="table-row" key={d.document_id} variants={item} whileHover={{ x: 4 }}>
              <span className="td-title">
                <Icon name="description" size={18} />
                {d.title}
              </span>
              <span>
                <span className={`status-dot ${d.status}`} />
                {d.status === "processing" ? (
                  <span className="status-processing">ingesting…</span>
                ) : (
                  d.status
                )}
              </span>
              <span className="mono">{d.chunk_count}</span>
              <span className="mono">{clock(d.created_at) || "—"}</span>
              <motion.button
                className="row-action"
                onClick={() => onDelete(d.document_id, d.title)}
                title="Delete"
                whileHover={{ scale: 1.15 }}
                whileTap={{ scale: 0.85 }}
              >
                <Icon name="delete" size={18} />
              </motion.button>
            </motion.div>
          ))}

          {data && data.length === 0 && uploading.length === 0 && (
            <div className="page-empty">No documents yet — drop a file to ingest your first.</div>
          )}
        </motion.div>
      )}

      {/* Full-page drop overlay. */}
      <AnimatePresence>
        {dragActive && (
          <motion.div
            className="drop-overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="drop-overlay-inner">
              <Icon name="upload_file" size={40} />
              <span>Drop to ingest</span>
              <small>PDF · DOCX · TXT · MD · CSV · PPTX · XLSX</small>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function DemoBanner({ endpoint }: { endpoint: string }) {
  return (
    <motion.div
      className="demo-banner"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: 0.1 }}
    >
      <Icon name="info" size={16} />
      Showing demo data. Wired to <code>{endpoint}</code> — set <code>VITE_DEMO_MODE=false</code> for live data.
    </motion.div>
  );
}
