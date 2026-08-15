/* ============================================================================
   Axiom — billing UI polish (additive).
   ----------------------------------------------------------------------------
   Loaded as a deferred classic script AFTER the main inline script in
   public/axiom.html. It patches three of the same views through VIEW_MAP
   (Agent API, Context Compiler, Pricing billing-status card) and keeps the
   plan/usage header chip fresh. It does NOT change the billing backend,
   the /consume contract, wallet addresses, the MCP implementation, or the
   engine. All server-authoritative behavior is preserved: the browser only
   renders state returned by the billing service.

   Why a separate file: the main single-file app is large and these are
   presentation-layer improvements to already-wired views. The original
   handlers still run (auto-demos, checkout, local fallback); the patches
   replace only the interactive bits listed below.
   ============================================================================ */

(function () {
  "use strict";

  /* ------------------------------------------------------------------ *
   * Shared authorization-state helpers (used by the Agent API console   *
   * and the Context Compiler). Distinguishes every server outcome so    *
   * the user never sees a misleading generic message.                   *
   * ------------------------------------------------------------------ */

  function axReasonLabel(auth) {
    if (auth.status === 503 || auth.reason === "billing_unavailable") return "UNAVAILABLE";
    if (auth.reason === "request_id_conflict") return "CONFLICT";
    if (auth.reason === "invalid_request_id" || auth.reason === "invalid_units" || auth.reason === "invalid_operation") return "REJECTED";
    if (auth.status === 0 || auth.reason === "network") return "UNREACHABLE";
    if (auth.reason === "server_error") return "ERROR";
    return "REJECTED";
  }

  function axReasonMessage(auth) {
    if (auth.reason === "allowance_exceeded") return "ALLOWANCE EXCEEDED — your plan has no remaining usage. Renew or upgrade to continue.";
    if (auth.reason === "expired_license") return "LICENSE EXPIRED — renew to continue.";
    if (auth.reason === "invalid_license") return "INVALID LICENSE — verify your payment or license.";
    if (auth.reason === "local_payment_only") return "LOCAL PAYMENT VERIFICATION ONLY — server authorization blocked.";
    if (auth.reason === "request_id_conflict") return "REQUEST CONFLICT — this operation was already recorded. Retry to get a fresh authorization.";
    if (auth.status === 503 || auth.reason === "billing_unavailable") return "BILLING SERVICE UNAVAILABLE — premium operations are blocked (fail closed). Try again shortly.";
    if (auth.status === 0 || auth.reason === "network") return "BILLING SERVICE UNREACHABLE — premium operations are blocked (fail closed).";
    if (auth.reason === "server_error") return "SERVER ERROR — authorization failed. Retry; if it persists, check the billing service configuration.";
    return "SERVER REJECTED — authorization denied. Check your plan, license, and billing service status.";
  }

  /* Server-verified reservation line, appended to success metadata. */
  function axServerMeta(srv) {
    if (!srv || !srv.allowed) return "";
    const consumed = (srv.consumed || 0).toLocaleString();
    const remaining = srv.remaining != null ? srv.remaining.toLocaleString() + " remaining" : "unlimited";
    return `<br><span style="color:var(--ok)">SERVER VERIFIED · reserved ${consumed} tok · ${remaining}</span>`;
  }

  /* ------------------------------------------------------------------ *
   * 1. Agent API console — authorization state is understandable,       *
   *    every server outcome is labeled, and success shows the           *
   *    server-reserved consumption (mirroring the Context Compiler).    *
   * ------------------------------------------------------------------ */

  async function axAgentRun() {
    const tsel = document.getElementById("tsel");
    const targs = document.getElementById("targs");
    const tout = document.getElementById("tout");
    const tmet = document.getElementById("tmet");
    if (!tsel || !targs || !tout || !tmet) return;
    const t = MCP_TOOLS.find(function (x) { return x.name === tsel.textContent; }) || MCP_TOOLS[0];
    let args = {};
    try { args = JSON.parse(targs.value || "{}"); } catch (e) { toast("Invalid JSON args"); return; }

    let auth = null;
    if (_axSkipUsage) {
      // Auto-demo run at view load: local engine demonstration, not billed.
      _axSkipUsage = false;
    } else {
      if (allowanceExceeded()) {
        toast("Token allowance exceeded — renew or upgrade in Pricing");
        tout.textContent = JSON.stringify({ error: "allowance_exceeded", note: "This plan's token allowance is exhausted. Renew or upgrade in the Pricing view." }, null, 2);
        return;
      }
      const est = estimateToolUnits(t, args);
      tmet.innerHTML = `Authorizing with billing service…`;
      auth = await serverConsume("agent.call", est);
      if (!auth.allowed) {
        const msg = axReasonMessage(auth);
        tout.textContent = JSON.stringify({ error: auth.reason || "billing_unavailable", note: msg, server: auth.message || "" }, null, 2);
        tmet.innerHTML = `SERVER ${axReasonLabel(auth)} · reserved ${est.toLocaleString()} tok not authorized`;
        toast("🔒 " + msg);
        return;
      }
    }

    const t0 = performance.now();
    let out;
    try { out = t.run(args); } catch (e) { out = { error: String(e) }; }
    const ms = (performance.now() - t0).toFixed(2);
    const used = tk(out), base = baselineTokensFor(out) || used * 10;
    try { emit("tool.invoked", { key: t.name, ms: +ms }); } catch (e) {}
    tout.textContent = JSON.stringify(out, null, 2);
    const srv = auth && auth.state ? auth.state : {};
    tmet.innerHTML = `${ms} ms · ${used.toLocaleString()} tok returned · vs ${base.toLocaleString()} raw · <span style="color:var(--ok)">${Math.max(1, Math.round(base / Math.max(1, used)))}× reduction</span>${axServerMeta(srv)}`;
  }

  /* ------------------------------------------------------------------ *
   * 2. Context Compiler — same authorization mapping + server meta.     *
   * ------------------------------------------------------------------ */

  async function axPackRun() {
    const pkq = document.getElementById("pkq");
    const pkb = document.getElementById("pkb");
    const pkout = document.getElementById("pkout");
    const pkmet = document.getElementById("pkmet");
    if (!pkq || !pkb || !pkout || !pkmet) return;
    const q = pkq.value.trim();
    if (!q) { toast("Describe the task first"); return; }
    if (allowanceExceeded()) {
      toast("Token allowance exceeded — renew or upgrade in Pricing");
      pkout.textContent = "Allowance exceeded — renew or upgrade in the Pricing view to compile packs.";
      return;
    }
    const budget = +pkb.value;
    const reserved = Math.max(1000, Math.min(32000, budget));
    pkout.textContent = "Authorizing with billing service…";
    const auth = await serverConsume("context.compile", reserved);
    if (!auth.allowed) {
      const msg = axReasonMessage(auth);
      pkout.textContent = msg;
      pkmet.innerHTML = `SERVER ${axReasonLabel(auth)} · ${escapeHtml(auth.message || "")}`;
      toast("🔒 " + msg);
      return;
    }
    const r = compilePack(q, budget);
    const base = repoTokens();
    const srv = auth.state || {};
    pkout.textContent = r.text;
    pkmet.innerHTML = `${r.used.toLocaleString()} tok observed · ${r.sections.length}/${r.considered} surfaces · vs ${base.toLocaleString()} raw repo · <span style="color:var(--ok)">${Math.max(1, Math.round(base / Math.max(1, r.used)))}× reduction</span>${axServerMeta(srv)}`;
    const pksel = document.getElementById("pksel");
    if (pksel) {
      pksel.innerHTML = r.sections.length
        ? r.sections.map(function (s) {
            return `<div class="fi" data-p="${escapeHtml(s.path)}"><span style="flex:1">${escapeHtml(s.path)}<div class="dim" style="font-size:11px">rank ${s.score.toFixed(1)} · ${s.cost} tok</div></span></div>`;
          }).join("")
        : `<div class="dim" style="padding:14px;font-size:12px">No surface matched — try different words.</div>`;
      pksel.querySelectorAll(".fi").forEach(function (el) { el.onclick = function () { openFile(el.dataset.p); }; });
    }
    try { emit("pack.compiled", { key: q, tokens: r.used }); } catch (e) {}
  }

  /* ------------------------------------------------------------------ *
   * 3. Pricing — billing-status card with explicit usage states         *
   *    (Healthy / Near limit / Exhausted) and an upgrade action when    *
   *    the allowance is exhausted. Rendered entirely from server-synced *
   *    state; the server remains the source of truth.                  *
   * ------------------------------------------------------------------ */

  function axUsageState(ui) {
    if (ui.unlimited) return null;
    if (ui.exceeded) return { k: "bad", label: "Exhausted" };
    if (ui.pct >= 80) return { k: "warn", label: "Near limit" };
    return { k: "ok", label: "Healthy" };
  }

  function axRenderBsBody() {
    const el = document.getElementById("bsBody");
    if (!el) return;
    const lic = license(), ui = usageInfo();
    const expired = !!lic.expired;
    const prev = lic.prev || null;
    const shownPlan = PLANS.find(function (x) { return x.id === (expired && prev ? prev.plan : lic.plan); }) || PLANS[0];
    const shownBilling = expired && prev ? prev.billing : lic.billing;
    const shownExpires = expired && prev ? prev.expires : lic.expires;
    let pend = null;
    try { pend = JSON.parse(localStorage.getItem(PEND_LS) || "null"); } catch (e) {}
    const vBadge = expired
      ? `<span style="color:var(--bad)">✗ expired — premium access off</span>`
      : lic.verified === "signed"
        ? `<span style="color:var(--ok)">✓ server-verified license</span>`
        : lic.verified === "client"
          ? `<span style="color:var(--warn)">⚠ local verification — billing service unreachable</span>`
          : `<span class="dim">not subscribed</span>`;
    const st = axUsageState(ui);
    el.innerHTML = `
      ${pend ? `<div style="background:var(--panel-2);border:1px solid var(--warn);color:var(--warn);border-radius:6px;padding:8px 10px;font-size:12px;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">⏳ <b>Payment detected</b> (${escapeHtml((pend.asset || "").toUpperCase())} · ${escapeHtml(pend.txid).slice(0, 12)}…) — awaiting on-chain confirmation. No license issued yet. <button id="bsRecheck" style="margin-left:auto">Re-check</button></div>` : ""}
      ${expired ? `<div style="background:var(--panel-2);border:1px solid var(--bad);color:var(--bad);border-radius:6px;padding:8px 10px;font-size:12px;margin-bottom:10px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">⚠ <b>License expired</b> — your ${escapeHtml(shownPlan.name)} license${shownBilling ? " (" + billingLabel(shownBilling) + ")" : ""} lapsed. Premium tools are locked until you renew. <button id="bsRenew" style="margin-left:auto">Renew with crypto</button></div>` : ""}
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:10px">
        <span class="chip"><b>${shownPlan.name}</b></span>
        ${shownBilling ? `<span class="chip">${billingLabel(shownBilling)}</span>` : ""}
        ${vBadge}
        ${_authState ? `<span class="chip" style="color:${_authState === "verified" ? "var(--ok)" : _authState === "rejected" ? "var(--bad)" : "var(--warn)"}">SERVER ${_authState === "verified" ? "VERIFIED" : _authState === "rejected" ? "REJECTED" : "UNAVAILABLE"}</span>` : ""}
        ${lic.paid != null ? `<span class="chip">${escapeHtml(lic.paid)} ${(lic.asset || "").toUpperCase()}${lic.confirmed ? " · confirmed" : " · provisional (0-conf)"}</span>` : ""}
        ${lic.txid ? `<span class="chip mono" title="${escapeHtml(lic.txid)}">tx ${escapeHtml(lic.txid).slice(0, 10)}…</span>` : ""}
      </div>
      ${(expired || lic.plan !== "free") ? `
      <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:8px">
        <div><div class="dim" style="font-size:11px">Status</div><div style="font-size:14px;color:${expired ? 'var(--bad)' : 'var(--ok)'}">${expired ? "Expired" : "Active"}</div></div>
        <div><div class="dim" style="font-size:11px">Renewal / expiration</div><div style="font-size:14px">${shownExpires ? new Date(shownExpires).toLocaleDateString() : "Lifetime"}</div></div>
        <div><div class="dim" style="font-size:11px">Payment status</div><div style="font-size:14px">${lic.confirmed === false ? "Pending confirmation" : "Verified on-chain"}</div></div>
      </div>` : ""}
      ${expired ? `
      <div class="dim" style="font-size:11px;margin-top:6px">Usage metering is paused while the license is expired. Renew to resume${shownPlan.unlimited ? " your unlimited allowance" : " your allowance of " + (allowanceOf(prev) ?? 0).toLocaleString() + " tokens"}.</div>`
      : `
      <div style="margin-top:6px">
        <div style="display:flex;justify-content:space-between;font-size:11px;margin-bottom:4px">
          <span class="dim">Token allowance</span>
          <span>${ui.unlimited ? "Unlimited" : `${ui.used.toLocaleString()} / ${ui.allowance.toLocaleString()} used${ui.exceeded ? ' <span style="color:var(--bad)">· exceeded</span>' : ""}`}</span>
        </div>
        ${ui.unlimited ? `<div class="dim" style="font-size:11px">No usage limit on this plan.</div>` : `
        <div style="height:8px;border-radius:4px;background:var(--panel-2);overflow:hidden"><div style="height:100%;width:${ui.pct}%;background:${ui.pct >= 100 ? "var(--bad)" : ui.pct >= 80 ? "var(--warn)" : "var(--accent)"}"></div></div>
        <div class="dim" style="font-size:11px;margin-top:4px">${st ? `<span style="color:var(--${st.k});font-weight:600">${st.label}</span> · ` : ""}${ui.exceeded ? "Allowance exhausted — renew or upgrade to continue using premium tooling." : ui.remaining.toLocaleString() + " tokens remaining this period."}</div>
        ${ui.exceeded ? `<button id="bsUpgrade" style="margin-top:8px;font-size:11px;padding:4px 10px">Renew / upgrade with crypto</button>` : ""}
        ${_authState === "unavailable" ? `<div class="dim" style="font-size:11px;margin-top:4px;color:var(--warn)">Billing service unavailable — premium operations are blocked (fail closed) until the service returns.</div>` : ""}`}
      </div>`}`;
    const rk = document.getElementById("bsRecheck");
    if (rk) rk.onclick = axRecheckPending;
    const up = document.getElementById("bsUpgrade");
    if (up) up.onclick = axOpenUpgrade;
    const rn = document.getElementById("bsRenew");
    if (rn) rn.onclick = axRenewPrevious;
  }

  /* Renew action for an expired license: reopen checkout pre-selected on the
     license's previous plan (same plan, new period) instead of pushing the
     user up a tier. Falls back to the checkout panel if the buy button is
     not present. */
  function axRenewPrevious() {
    const lic = license();
    const prev = lic && lic.prev ? lic.prev : null;
    const target = prev ? (PLANS.find(function (x) { return x.id === prev.plan; }) || null) : null;
    const btn = target ? document.querySelector('.buyBtn[data-p="' + target.id + '"]') : null;
    if (btn) { btn.click(); return; }
    const box = document.getElementById("paybox");
    if (box) { box.style.display = ""; box.scrollIntoView({ behavior: "smooth", block: "center" }); }
  }

  async function axRecheckPending() {
    let pend = null;
    try { pend = JSON.parse(localStorage.getItem(PEND_LS) || "null"); } catch (e) {}
    const el = document.getElementById("bsBody");
    if (!pend || !el) return;
    el.innerHTML = `<span class="dim">Re-checking payment confirmation…</span>`;
    const res = await billingPost("/api/billing/verify", { txid: pend.txid, asset: pend.asset, plan: pend.plan, billing: pend.billing });
    if (res.json && res.json.state === "pending") {
      toast("⏳ Still awaiting confirmation — re-check again shortly.");
      axRenderBsBody();
      return;
    }
    if (res.ok && res.json && res.json.ok) {
      const j = res.json, p = PLANS.find(function (x) { return x.id === pend.plan; }) || PLANS[0];
      try { localStorage.removeItem(PEND_LS); } catch (e) {}
      grant(pend.plan, pend.asset, pend.txid, { billing: pend.billing, license: j.license, verified: "signed", paid: j.paid, confirmed: j.confirmed, note: j.note });
      toast(`✓ ${p.name} unlocked — signed license issued`);
      axRenderBsBody();
      return;
    }
    try { localStorage.removeItem(PEND_LS); } catch (e) {}
    axRenderBsBody();
    toast("⚠ Payment could not be confirmed — re-submit it from Pricing.");
  }

  async function axRefreshStatus() {
    const el = document.getElementById("bsBody");
    if (!el) return;
    const lic = license();
    if (lic && lic.license && lic.verified === "signed") {
      el.innerHTML = `<span class="dim">Checking license with billing service…</span>`;
      try {
        const r = await fetch(BILLING_BASE + "/api/billing/status?license=" + encodeURIComponent(lic.license));
        const j = await r.json().catch(function () { return {}; });
        if (r.ok && j.ok && j.used != null) {
          const u = getUsage();
          u.used = j.used;
          try { localStorage.setItem(USG_LS, JSON.stringify(u)); } catch (e) {}
        }
      } catch (e) {}
    }
    axRenderBsBody();
  }

  /* Upgrade action: reuse the checkout the original view wired up by
     clicking the target plan's buy button (closure state stays intact). */
  function axOpenUpgrade() {
    const t = tier();
    const target = PLANS.find(function (x) { return x.rank === Math.min(3, (RANK[t] || 0) + 1); }) || PLANS.find(function (x) { return x.id === "pro"; });
    if (!target) return;
    const btn = document.querySelector('.buyBtn[data-p="' + target.id + '"]');
    if (btn) { btn.click(); return; }
    const box = document.getElementById("paybox");
    if (box) { box.style.display = ""; box.scrollIntoView({ behavior: "smooth", block: "center" }); }
  }

  /* ------------------------------------------------------------------ *
   * 4. Wire the patches into the same views (additive — the originals   *
   *    still render the templates; only the interactive handlers are    *
   *    replaced with the improved ones).                                *
   * ------------------------------------------------------------------ */

  const axOrigMcp = VIEW_MAP.mcp;
  VIEW_MAP.mcp = function (root) {
    axOrigMcp(root);
    const btn = document.getElementById("trun");
    if (btn && !btn.dataset.ax) { btn.dataset.ax = "1"; btn.onclick = axAgentRun; }
  };

  const axOrigPack = VIEW_MAP.pack;
  VIEW_MAP.pack = function (root) {
    axOrigPack(root);
    const btn = document.getElementById("pkgo");
    if (btn && !btn.dataset.ax) { btn.dataset.ax = "1"; btn.onclick = axPackRun; }
    const inp = document.getElementById("pkq");
    if (inp && !inp.dataset.ax) { inp.dataset.ax = "1"; inp.onkeydown = function (e) { if (e.key === "Enter") axPackRun(); }; }
  };

  const axOrigPricing = VIEW_MAP.pricing;
  VIEW_MAP.pricing = function (root, ctx) {
    axOrigPricing(root, ctx);
    const refresh = document.getElementById("bsRefresh");
    if (refresh && !refresh.dataset.ax) { refresh.dataset.ax = "1"; refresh.onclick = axRefreshStatus; }
    axRenderBsBody();
    axFixExpiredHeader(root);
  };

  /* Expired license: the fallback view renders the "Current plan" chip from
     the wrapper license, whose plan resets to "free" on expiry — so it would
     read "Community". Correct that chip to name the plan that lapsed, drop the
     now-redundant "Previous license expired" chip, and color the state red.
     Presentation only: the billing service remains the entitlement authority. */
  function axFixExpiredHeader(root) {
    try {
      const lic = license();
      if (!lic || !lic.expired || !root) return;
      const prevPlan = lic.prev ? PLANS.find(function (x) { return x.id === lic.prev.plan; }) : null;
      const prevName = prevPlan ? prevPlan.name : "Pro";
      root.querySelectorAll(".chip").forEach(function (c) {
        const txt = (c.textContent || "").replace(/\s+/g, " ").trim();
        if (txt.indexOf("Current plan:") === 0) {
          c.innerHTML = '<span class="dot bad"></span>' + escapeHtml(prevName) + ' license <b style="color:var(--bad)">expired</b>';
          c.style.borderColor = "var(--bad)";
        } else if (txt.indexOf("Previous license expired") === 0) {
          c.remove();
        }
      });
    } catch (e) {}
  }

  /* Keep the header plan/usage chip fresh after every authorization, and
     normalize ambiguous server outcomes so the UI labels them precisely:
     HTTP >= 500 (or an "internal" error body) is a server error; HTTP 404
     means the billing service is not deployed/reachable. */
  const axOrigConsume = serverConsume;
  serverConsume = function (operation, units) {
    return axOrigConsume(operation, units).then(function (r) {
      try { if (typeof paintPlanChip === "function") paintPlanChip(); } catch (e) {}
      if (!r.allowed) {
        if (r.status >= 500 || (r.state && r.state.error === "internal")) r.reason = "server_error";
        else if (r.status === 404) r.reason = "billing_unavailable";
      }
      return r;
    });
  };

  /* If the current view is one we patched (e.g. a deep reload), re-render
     once so the improved handlers are active immediately. */
  try {
    if (state.repo && (state.route === "mcp" || state.route === "pack" || state.route === "pricing")) render();
  } catch (e) {}
})();
