import { useRef } from "react";
import { api } from "../api/client";
import { useResource } from "../hooks/useResource";
import { useBackend } from "../backend";
import { DEMO_DOCS } from "../mock/dataset";
import { Icon } from "../components/Icon";
import { clock } from "../util";

export function KnowledgeView() {
  const { call } = useBackend();
  const { data, loading, error, demo, reload } = useResource(
    () => api.listDocuments().then((r) => r.documents),
    DEMO_DOCS,
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
      <div className="section-page-head">
        <div>
          <h1 className="page-title">Knowledge</h1>
          <p className="page-sub">Documents Relay retrieves from. Drop a file to ingest it.</p>
        </div>
        <button className="btn-primary" onClick={() => fileRef.current?.click()}>
          <Icon name="upload_file" size={16} />
          Upload document
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt"
          hidden
          onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
        />
      </div>

      {demo && <DemoBanner endpoint="GET /documents" />}
      {loading && <div className="page-empty">Loading documents…</div>}
      {error && <div className="page-empty error">Couldn’t load documents — {error}</div>}

      {data && (
        <div className="card-surface table">
          <div className="table-head">
            <span>Title</span>
            <span>Status</span>
            <span>Chunks</span>
            <span>Created</span>
            <span></span>
          </div>
          {data.map((d) => (
            <div className="table-row" key={d.document_id}>
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
              <button className="row-action" onClick={() => onDelete(d.document_id, d.title)} title="Delete">
                <Icon name="delete" size={18} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function DemoBanner({ endpoint }: { endpoint: string }) {
  return (
    <div className="demo-banner">
      <Icon name="info" size={16} />
      Showing demo data. Wired to <code>{endpoint}</code> — connect the gateway (VITE_USE_MOCK=false) for live data.
    </div>
  );
}
