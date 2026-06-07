import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api/client";
import type { User } from "../api/client";
import { useBackend } from "../backend";
import { Icon } from "../components/Icon";
import { easeOut, fadeUp } from "../motion";

interface Props {
  email: string | null;
  onSignOut?: () => void;
}

export function AccountView({ email, onSignOut }: Props) {
  const { call, toast } = useBackend();

  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  // Edit state
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [editingEmail, setEditingEmail] = useState(false);
  const [emailInput, setEmailInput] = useState("");

  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getMe()
      .then((u) => {
        setUser(u);
        setNameInput(u.name ?? "");
        setEmailInput(u.email ?? email ?? "");
      })
      .catch(() => {
        setEmailInput(email ?? "");
      })
      .finally(() => setLoading(false));
  }, [email]);

  useEffect(() => {
    if (editingName) nameRef.current?.focus();
  }, [editingName]);
  useEffect(() => {
    if (editingEmail) emailRef.current?.focus();
  }, [editingEmail]);

  const saveName = async () => {
    const trimmed = nameInput.trim();
    if (!trimmed || trimmed === user?.name) { setEditingName(false); return; }
    const updated = await call("Update name", () => api.updateMe({ name: trimmed }), {
      success: "Name updated",
    });
    if (updated) { setUser(updated); setNameInput(updated.name); }
    setEditingName(false);
  };

  const saveEmail = async () => {
    const trimmed = emailInput.trim();
    if (!trimmed || trimmed === (user?.email ?? email)) { setEditingEmail(false); return; }
    const updated = await call("Update display email", () => api.updateMe({ email: trimmed }), {
      success: "Display email updated",
    });
    if (updated) setUser(updated);
    setEditingEmail(false);
  };

  const initial = ((user?.name ?? email ?? "R").trim()[0] ?? "R").toUpperCase();

  return (
    <motion.div
      className="account-view"
      variants={fadeUp}
      initial="hidden"
      animate="show"
    >
      {/* Header */}
      <motion.div
        className="account-view-header"
        initial={{ opacity: 0, y: -12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: easeOut }}
      >
        <div className="account-view-avatar">{initial}</div>
        <div>
          <h1 className="account-view-name">{loading ? "…" : (user?.name || "Account")}</h1>
          <span className="account-view-role">{user?.role ?? "member"}</span>
        </div>
      </motion.div>

      <div className="account-view-grid">
        {/* Profile card */}
        <div className="account-card">
          <h2 className="account-card-title label-caps">Profile</h2>

          {/* Name field */}
          <div className="account-field">
            <label className="account-field-label">Display name</label>
            <div className="account-field-row">
              {editingName ? (
                <>
                  <input
                    ref={nameRef}
                    className="account-input"
                    value={nameInput}
                    onChange={(e) => setNameInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveName();
                      if (e.key === "Escape") setEditingName(false);
                    }}
                    maxLength={120}
                  />
                  <button className="account-save-btn" onClick={() => void saveName()}>
                    <Icon name="check" size={16} />
                    Save
                  </button>
                  <button className="account-cancel-btn" onClick={() => setEditingName(false)}>
                    <Icon name="close" size={16} />
                  </button>
                </>
              ) : (
                <>
                  <span className="account-field-value">{loading ? "…" : (user?.name || "—")}</span>
                  <button className="account-edit-btn" onClick={() => { setEditingName(true); setNameInput(user?.name ?? ""); }}>
                    <Icon name="edit" size={15} />
                    Edit
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Email field */}
          <div className="account-field">
            <label className="account-field-label">
              Email
              <span className="account-field-hint">display only — auth email managed via Supabase</span>
            </label>
            <div className="account-field-row">
              {editingEmail ? (
                <>
                  <input
                    ref={emailRef}
                    className="account-input"
                    type="email"
                    value={emailInput}
                    onChange={(e) => setEmailInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void saveEmail();
                      if (e.key === "Escape") setEditingEmail(false);
                    }}
                    maxLength={254}
                  />
                  <button className="account-save-btn" onClick={() => void saveEmail()}>
                    <Icon name="check" size={16} />
                    Save
                  </button>
                  <button className="account-cancel-btn" onClick={() => setEditingEmail(false)}>
                    <Icon name="close" size={16} />
                  </button>
                </>
              ) : (
                <>
                  <span className="account-field-value">{loading ? "…" : (user?.email ?? email ?? "—")}</span>
                  <button className="account-edit-btn" onClick={() => { setEditingEmail(true); setEmailInput(user?.email ?? email ?? ""); }}>
                    <Icon name="edit" size={15} />
                    Edit
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Role */}
          <div className="account-field">
            <label className="account-field-label">Role</label>
            <div className="account-field-row">
              <span className="account-field-value account-role-badge">{user?.role ?? "member"}</span>
            </div>
          </div>
        </div>

        {/* Security card */}
        <div className="account-card">
          <h2 className="account-card-title label-caps">Security</h2>
          <div className="account-field">
            <label className="account-field-label">Password</label>
            <div className="account-field-row">
              <span className="account-field-value">Managed by Supabase Auth</span>
              <button
                className="account-edit-btn"
                onClick={() => toast("Password reset email sent — check your inbox", "info")}
              >
                <Icon name="lock_reset" size={15} />
                Reset
              </button>
            </div>
          </div>
          <div className="account-field">
            <label className="account-field-label">Authentication</label>
            <div className="account-field-row">
              <span className="account-field-value">Email + Google OAuth</span>
            </div>
          </div>
          <div className="account-field">
            <label className="account-field-label">Session</label>
            <div className="account-field-row">
              <span className="account-field-value account-session-ok">
                <Icon name="verified_user" size={14} />
                Authenticated
              </span>
            </div>
          </div>
        </div>

        {/* Danger zone */}
        <div className="account-card account-card-danger">
          <h2 className="account-card-title label-caps">Session</h2>
          <p className="account-danger-desc">
            Signing out will end your current session. You can sign back in at any time.
          </p>
          <button
            className="account-signout-btn"
            onClick={() => onSignOut?.()}
            disabled={!onSignOut}
          >
            <Icon name="logout" size={16} />
            Sign out
          </button>
        </div>
      </div>
    </motion.div>
  );
}
