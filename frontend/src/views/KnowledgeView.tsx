import { useRef } from "react";
import { motion } from "framer-motion";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { useBackend } from "../backend";
import { Icon } from "../components/Icon";
import { clock } from "../util";
import { fadeUp, inView, item, pressable, staggerParent } from "../motion";

export function KnowledgeView() {
  const { call } = useBackend();
  const { data, loading, error, reload } = useResource(() =>
    api.listDocuments().then((r) => r.documents),
  );
  const fileRef = useRef<HTMLInputElement>(null);

  const onUpload = async (file: File) => {
    await call("Upload document", () => api.uploadDocument(file, file.name), {
      endpoint: "POST /documents",
      success: `Ingesting ${file.name}…`,
    });
    reload();
  };

  const onDelete = async (id: string, title: string) => {
    await call("Delete document", () => api.deleteDocument(id), {
      endpoint: `DELETE /documents/${id}`,
      success: `Removed ${title}`,
    });
    reload();
  };

  return (
    <div className="section-page">
      <motion.div className="section-page-head" variants={fadeUp} initial="hidden" animate="show">
        <div>
          <h1 className="page-title">Knowledge</h1>
          <p className="page-sub">Documents Relay retrieves from. Drop a file to ingest it.</p>
        </div>
        <motion.button className="btn-primary" onClick={() => fileRef.current?.click()} {...pressable}>
          <Icon name="upload_file" size={16} />
          Upload document
        </motion.button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt"
          hidden
          onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
        />
      </motion.div>

      {loading && <div className="page-empty">Loading documents…</div>}
      {error && <div className="page-empty error">Couldn’t load documents — {error}</div>}
      {data && data.length === 0 && (
        <div className="page-empty">No documents yet. Upload one to build Relay’s knowledge.</div>
      )}

      {data && (
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
          {data.map((d) => (
            <motion.div className="table-row" key={d.document_id} variants={item} whileHover={{ x: 4 }}>
              <span className="td-title">
                <Icon name="description" size={18} />
                {d.title}
              </span>
              <span>
                <span className={`status-dot ${d.status}`} />
                {d.status}
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
        </motion.div>
      )}
    </div>
  );
}
