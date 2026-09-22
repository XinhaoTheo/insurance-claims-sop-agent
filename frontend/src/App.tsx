import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';

type ApiProtocol = 'openai' | 'anthropic';
type Config = { api_protocol: ApiProtocol; protocol_base_urls: Record<ApiProtocol, string>; base_url: string | null; model: string | null; configured: boolean; demo_date: string; email_mode: string; hosted: boolean; sponsored: boolean; allowed_model_base_urls: string[] | null };
type Credentials = { session_id: string; access_token: string };
type CallerAction = 'send_summary' | 'skip_summary' | 'finish_case';
type OutgoingTurn = { message: string; turn_id: string; caller_action?: CallerAction };
type Snapshot = {
  session_id: string; model_configured: boolean;
  state: { phase: string; status: string; verified: boolean; matched_fields: string[]; identity_collected: string[]; intent: string | null; case_hints: Record<string, string | number>; selected_case_id: string | null; email_status: string; demo_date: string; pending: string | null };
  messages: { role: 'user' | 'assistant'; content: string; turn_id: string }[];
  events: { kind: string; phase: string; detail: string; at: string }[];
  email_summary: { subject: string; body: string; to_masked: string; status: string } | null;
};
type CreatedSession = Snapshot & Credentials;
const STORAGE_KEY = 'claims-companion-session';
const PHASES = [
  { id: 'VERIFY_ID', title: 'Verify identity', description: 'Protect customer information', icon: 'shield' },
  { id: 'RESOLVE_INTENT', title: 'Understand the request', description: 'Use context to find the right case', icon: 'search' },
  { id: 'PROCESS_CASE', title: 'Support the claim', description: 'Answer using verified case data', icon: 'file' },
  { id: 'POST_PROCESS', title: 'Wrap up together', description: 'Offer an optional email summary', icon: 'mail' },
] as const;
const COMPLETE_SAMPLE = 'I’m the policyholder. My name is Margaret Chen, policy POL-9921. I’m calling about my denied healthcare claim from January. DOB is 1985-03-15, SSN last four is 4472.';
const PARTIAL_SAMPLE = 'My name is Margaret Chen. I’m calling about my denied healthcare claim from January, but I don’t want to share my SSN.';
const LABELS: Record<string, string> = { name: 'Full name', dob: 'Date of birth', phone: 'Phone', email: 'Email', ssn_last4: 'SSN last four' };

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    shield: <><path d="M12 3 4 6v6c0 4 8 9 8 9s8-5 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/></>,
    search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
    file: <><path d="M14 3H5v18h14V8l-5-5Z"/><path d="M14 3v5h5M8 12h8M8 16h6"/></>,
    mail: <><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/></>,
    send: <><path d="m3 11 18-8-8 18-2-8-8-2Z"/><path d="m11 13 10-10"/></>,
    plus: <path d="M12 5v14M5 12h14"/>,
    close: <path d="m6 6 12 12M6 18 18 6"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    key: <><circle cx="8" cy="9" r="5"/><path d="m12 13 8 8m-4-4 3-3m-6 0 3-3"/></>,
    arrow: <path d="M4 12h16m-6-6 6 6-6 6"/>,
    spark: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/></>,
    info: <><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}
class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
async function api<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(path, { ...options, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } });
  let body: { detail?: string };
  try { body = await response.json(); }
  catch {
    if (response.ok) throw new ApiError('The server returned an invalid JSON response. Please retry.', response.status);
    body = {};
  }
  if (!response.ok) throw new ApiError(body?.detail ?? (response.statusText || 'The request failed.'), response.status);
  return body as T;
}
function display(value: string | number): string { return String(value).replaceAll('_', ' '); }
function savedCredentials(): Credentials | null {
  const saved = sessionStorage.getItem(STORAGE_KEY);
  if (!saved) return null;
  try { return JSON.parse(saved); } catch { sessionStorage.removeItem(STORAGE_KEY); return null; }
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [credentials, setCredentials] = useState<Credentials | null>(null);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [booting, setBooting] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [modal, setModal] = useState(false);
  const [tab, setTab] = useState<'overview' | 'activity'>('overview');
  const [apiKey, setApiKey] = useState('');
  const [apiProtocol, setApiProtocol] = useState<ApiProtocol>('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [model, setModel] = useState('');
  const [modelBusy, setModelBusy] = useState(false);
  const [modelError, setModelError] = useState('');
  const [modelMessage, setModelMessage] = useState('');
  const started = useRef(false);
  const bottom = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const pendingTurn = useRef<OutgoingTurn | null>(null);

  function retainSession(data: CreatedSession) {
    setSnapshot(data);
    const saved = { session_id: data.session_id, access_token: data.access_token };
    setCredentials(saved);
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  }
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const defaults = await api<Config>('/api/config');
        setConfig(defaults); setApiProtocol(defaults.api_protocol); setBaseUrl(defaults.base_url ?? ''); setModel(defaults.model ?? '');
        const saved = savedCredentials();
        if (saved) {
          try {
            const existing = await api<Snapshot>(`/api/sessions/${saved.session_id}`, {}, saved.access_token);
            setCredentials(saved); setSnapshot(existing);
            return;
          } catch (e) {
            if (!(e instanceof ApiError) || e.status !== 404) throw e;
            sessionStorage.removeItem(STORAGE_KEY);
            setNotice('Your previous conversation is no longer available. A new conversation has been started.');
          }
        }
        retainSession(await api<CreatedSession>('/api/sessions', { method: 'POST', body: JSON.stringify({}) }));
      } catch (e) { setError((e as Error).message); }
      finally { setBooting(false); }
    })();
  }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [snapshot?.messages.length, busy]);
  useEffect(() => { if (modal) dialog.current?.showModal(); else dialog.current?.close(); }, [modal]);

  async function newSession() {
    if (busy || modelBusy) return;
    setBusy(true); setError(''); setInput(''); pendingTurn.current = null;
    try {
      const next = await api<CreatedSession>('/api/sessions', { method: 'POST', body: JSON.stringify({}) });
      retainSession(next);
      setNotice(next.model_configured ? 'New conversation started with the configured server model.' : 'New conversation created. Connect your AI model to start chatting.');
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function send(message = input, callerAction?: CallerAction) {
    const clean = message.trim();
    if (!clean || busy || modelBusy || !snapshot?.model_configured) return;
    const session = credentials!;
    setBusy(true); setError(''); setNotice('');
    const payload: OutgoingTurn = pendingTurn.current?.message === clean && pendingTurn.current.caller_action === callerAction
      ? pendingTurn.current
      : { message: clean, turn_id: crypto.randomUUID(), ...(callerAction ? { caller_action: callerAction } : {}) };
    pendingTurn.current = payload;
    try {
      const next = await api<Snapshot>(`/api/sessions/${session.session_id}/messages`, { method: 'POST', body: JSON.stringify(payload) }, session.access_token);
      setSnapshot(next); setInput(''); pendingTurn.current = null;
    } catch (e) {
      setError((e as Error).message); setInput(clean);
      // Refresh the server-reported model configuration after a failed turn.
      try { setSnapshot(await api<Snapshot>(`/api/sessions/${session.session_id}`, {}, session.access_token)); }
      catch (refreshError) { setError((refreshError as Error).message); }
    }
    finally { setBusy(false); textarea.current?.focus(); }
  }
  function openSettings() {
    setModelError(''); setModelMessage(''); setApiKey(''); setModal(true);
  }
  function closeSettings() { if (!modelBusy) { setApiKey(''); setModal(false); } }
  async function modelAction(testOnly = false) {
    if (modelBusy || busy) return;
    const session = credentials!;
    setModelBusy(true); setModelError(''); setModelMessage('');
    const payload = { api_protocol: apiProtocol, ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}), ...(baseUrl.trim() ? { base_url: baseUrl.trim() } : {}), ...(model.trim() ? { model: model.trim() } : {}) };
    try {
      if (testOnly) {
        const result = await api<{ message: string }>('/api/models/test', { method: 'POST', body: JSON.stringify(payload) });
        setModelMessage(`${result.message} You can now apply it to this session.`);
      } else {
        const next = await api<Snapshot>(`/api/sessions/${session.session_id}/model`, { method: 'POST', body: JSON.stringify(payload) }, session.access_token);
        setSnapshot(next); setApiKey(''); setModal(false); setNotice('AI model configured for this session. You can start chatting.');
      }
    } catch (e) { setModelError((e as Error).message); }
    finally { setModelBusy(false); }
  }
  async function disconnectModel() {
    if (modelBusy || busy) return;
    const session = credentials!;
    setModelBusy(true); setModelError(''); setModelMessage('');
    try {
      setSnapshot(await api<Snapshot>(`/api/sessions/${session.session_id}/model`, { method: 'DELETE' }, session.access_token));
      setModal(false); setNotice('Model disconnected and temporary credentials cleared. Reconnect to continue this conversation.');
    } catch (e) { setModelError((e as Error).message); }
    finally { setApiKey(''); setModelBusy(false); }
  }
  function changeProtocol(value: ApiProtocol) {
    const defaults = config!;
    const endpoint = defaults.protocol_base_urls[value];
    const allowed = defaults.allowed_model_base_urls;
    setApiProtocol(value); setBaseUrl(allowed && !allowed.includes(endpoint) ? (allowed[0] ?? endpoint) : endpoint);
    setModel(''); setApiKey(''); setModelError(''); setModelMessage('');
  }
  function fillSample(value: string) { setInput(value); textarea.current?.focus(); }
  const state = snapshot?.state;
  const phaseIndex = PHASES.findIndex(p => p.id === state?.phase);
  const modelConfigured = snapshot ? snapshot.model_configured : false;
  const canChat = modelConfigured && !busy && !modelBusy;
  const isClosed = state?.status === 'completed' || state?.status === 'handoff_requested';
  const identityFields = state ? state.identity_collected : [];
  const matchedFields = state ? state.matched_fields : [];
  const hints = state ? Object.entries(state.case_hints) : [];
  const messages = snapshot ? snapshot.messages : [];
  const events = snapshot ? snapshot.events : [];

  return <div className="app-shell">
    <header className="topbar">
      <a className="brand" href="#" onClick={e => e.preventDefault()} aria-label="Claims Companion home"><span className="brand-mark"><Icon name="shield" size={25}/></span><span>Claims Companion<small>INSURANCE SUPPORT · SOP DEMO</small></span></a>
      <div className="header-actions"><span className="environment-badge"><span/>{config ? config.hosted ? 'Hosted demo' : 'Local workspace' : 'Connecting…'}</span>{!config?.sponsored && <button className="button subtle" aria-label="Model settings" onClick={openSettings} disabled={booting || busy || !credentials}><Icon name="key" size={17}/><span>Model settings</span></button>}<button className="button new-button" aria-label="New conversation" onClick={newSession} disabled={busy || booting || !config}><Icon name="plus" size={17}/><span>New conversation</span></button></div>
    </header>
    <main className="workspace">
      <section className="conversation-panel" aria-label="Customer conversation">
        <div className="conversation-heading"><div><div className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</div><h1>Let’s work through your claim.</h1><p>A natural conversation. A protected, step-by-step process.</p></div><span className="conversation-emblem"><Icon name="spark" size={27}/></span></div>
        <div className={`connection-banner ${modelConfigured || isClosed ? 'configured' : ''}`}><Icon name={isClosed ? 'check' : modelConfigured ? 'spark' : 'key'} size={16}/><span>{isClosed ? state?.status === 'handoff_requested' ? 'Conversation closed · simulated human support requested' : 'Conversation complete · start a new conversation whenever you’re ready' : booting ? 'Preparing your model connection…' : modelConfigured ? config?.sponsored ? 'Ready to chat · model access provided by the demo owner' : 'AI model configured · business rules enforced by the server' : 'Connect an AI model before starting the conversation.'}</span>{!modelConfigured && !isClosed && !config?.sponsored && <button onClick={openSettings} disabled={booting || !credentials}>Configure model <Icon name="arrow" size={13}/></button>}</div>
        <div className="messages" role="log" aria-label="Conversation messages" aria-live="polite">
          {booting && <div className="welcome-state"><div className="loading-dots"><i/><i/><i/></div><p>Preparing your conversation…</p></div>}
          {!booting && !messages.length && <div className="welcome-state"><span className="welcome-icon"><Icon name="shield" size={28}/></span><h2>Claim support, with care.</h2><p>We’ll verify your identity first, then help you understand your claim and next steps.</p></div>}
          {messages.map((message) => <div className={`message-row ${message.role}`} key={`${message.turn_id}-${message.role}`}>
            {message.role === 'assistant' && <span className="avatar"><Icon name="shield" size={18}/></span>}
            <div className="message-body"><div className="message-label">{message.role === 'assistant' ? 'Claims Companion' : 'You'}</div><div className="bubble">{message.content}</div></div>
          </div>)}
          {busy && <div className="message-row assistant"><span className="avatar"><Icon name="shield" size={18}/></span><div className="bubble thinking"><span className="loading-dots"><i/><i/><i/></span><span>Working through the next step</span></div></div>}
          {snapshot?.email_summary && <div className="email-preview"><div className="preview-label"><Icon name="mail" size={16}/><strong>Email summary</strong><span className="badge amber">MOCK EMAIL</span></div><h3>{snapshot.email_summary.subject}</h3><p className="email-meta">To: {snapshot.email_summary.to_masked} · {display(snapshot.email_summary.status)}</p><div className="email-body">{snapshot.email_summary.body}</div><small>This demo does not deliver email to a real inbox.</small></div>}
          <div ref={bottom}/>
        </div>
        <div className="composer-area">
          {notice && <div className="notice" role="status"><Icon name="check" size={15}/><span>{notice}</span><button aria-label="Dismiss notice" onClick={() => setNotice('')}><Icon name="close" size={14}/></button></div>}
          {error && <div className="error-banner" role="alert"><span>{error}</span>{!credentials ? <button onClick={() => window.location.reload()}>Retry</button> : pendingTurn.current ? <button onClick={() => send(pendingTurn.current!.message, pendingTurn.current!.caller_action)} disabled={!canChat}>Retry message</button> : <button onClick={() => setError('')}>Dismiss</button>}</div>}
          {state?.phase === 'POST_PROCESS' && !isClosed && <div className="action-choices"><button className="button primary" disabled={!canChat} onClick={() => send('Yes, send the email summary to my verified email.', 'send_summary')}><Icon name="mail" size={16}/>Send mock email</button><button className="button subtle" disabled={!canChat} onClick={() => send('Skip the email.', 'skip_summary')}>Skip email</button></div>}
          {state?.phase === 'PROCESS_CASE' && !isClosed && <div className="action-choices"><button className="chip" disabled={!canChat} onClick={() => send('That is all, please summarize.', 'finish_case')}>I’m all set — wrap up <Icon name="arrow" size={14}/></button></div>}
          {(!state || state.phase === 'VERIFY_ID') && !isClosed && <div className="sample-row"><span>TRY A SCENARIO</span><button className="chip" onClick={() => fillSample(COMPLETE_SAMPLE)} disabled={!canChat}>Complete identity <Icon name="plus" size={13}/></button><button className="chip" onClick={() => fillSample(PARTIAL_SAMPLE)} disabled={!canChat}>Partial answer & refusal <Icon name="plus" size={13}/></button></div>}
          {isClosed ? <div className="closed-state"><Icon name="check" size={18}/><span>{state?.status === 'handoff_requested' ? 'This conversation has been prepared for human support.' : 'This conversation has ended.'}</span><button onClick={newSession}>Start a new one</button></div> : <form className="composer" onSubmit={(e: FormEvent) => { e.preventDefault(); void send(); }}>
            <label className="sr-only" htmlFor="message">Your message</label><textarea id="message" ref={textarea} value={input} onChange={e => setInput(e.target.value)} placeholder={modelConfigured ? "Tell us what you need help with…" : "Configure your AI model to start chatting…"} rows={2} disabled={!canChat} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }}/><button type="submit" className="send-button" aria-label="Send message" disabled={!input.trim() || !canChat}><Icon name="send" size={20}/></button>
          </form>}
          <div className="composer-footer"><span><Icon name="shield" size={12}/> Synthetic customer data · use demo identities only</span><span>Enter to send · Shift + Enter for a new line</span></div>
        </div>
      </section>
      <aside className="inspector" aria-label="Read-only workflow inspector">
        <div className="inspector-header"><div><div className="eyebrow">BEHIND THE CONVERSATION</div><h2>Workflow, made visible.</h2></div><span className="read-only">Read only</span></div>
        <div className="inspector-tabs" role="tablist" aria-label="Workflow information"><button role="tab" aria-selected={tab === 'overview'} onClick={() => setTab('overview')} className={tab === 'overview' ? 'active' : ''}>Overview</button><button role="tab" aria-selected={tab === 'activity'} onClick={() => setTab('activity')} className={tab === 'activity' ? 'active' : ''}>Activity <span>{events.length}</span></button></div>
        <div className="inspector-content">
          {tab === 'overview' ? <>
            <section className="inspector-section"><div className="section-heading"><h3>THE FOUR-STEP SOP</h3><span className="badge purple">{phaseIndex >= 0 ? `${phaseIndex + 1} OF 4` : 'READY'}</span></div><ol className="phase-list">{PHASES.map((phase, index) => <li key={phase.id} className={`${index === phaseIndex ? 'current' : ''} ${index < phaseIndex ? 'done' : ''}`} aria-current={index === phaseIndex ? 'step' : undefined}><span className="phase-node"><Icon name={index < phaseIndex ? 'check' : phase.icon} size={18}/></span><div><strong>{phase.title}</strong><p>{phase.description}</p>{index === phaseIndex && <span className="current-label">{isClosed ? display(state!.status) : modelConfigured ? 'IN PROGRESS' : 'AWAITING MODEL'}</span>}</div></li>)}</ol></section>
            <section className="inspector-section identity-section"><div className="section-heading"><h3>IDENTITY CHECK</h3><span className={`badge ${state?.verified ? 'green' : 'neutral'}`}>{state?.verified ? 'VERIFIED' : 'PROTECTED'}</span></div><div className="identity-number"><strong>{Math.min(matchedFields.length, 3)}<span> / 3</span></strong><span>distinct fields matched</span></div><div className="identity-bars" aria-label={`${matchedFields.length} of 3 identity fields matched`}>{[0, 1, 2].map(i => <span key={i} className={(matchedFields.length) > i ? 'filled' : ''}/>)}</div><p className="muted-note">Claim details stay protected until identity is verified.</p>{identityFields.length > 0 && <div className="field-chips">{identityFields.map(field => <span key={field}><Icon name={matchedFields.includes(field) ? 'check' : 'file'} size={12}/>{LABELS[field]}</span>)}</div>}</section>
            <section className="inspector-section"><div className="section-heading"><h3>CONVERSATION MEMORY</h3><Icon name="file" size={16}/></div><p className="muted-note">Useful context is remembered across steps. Identity values are hidden.</p><dl className="memory-list"><div><dt>Intent</dt><dd>{state?.intent ? display(state.intent) : 'Not established yet'}</dd></div><div><dt>Selected case</dt><dd>{state?.selected_case_id ?? 'Waiting for a verified match'}</dd></div>{hints.map(([key, value]) => <div key={key}><dt>{display(key)}</dt><dd>{display(value)}</dd></div>)}</dl>{state?.pending && <div className="pending-note"><span>AWAITING</span>{display(state.pending)}</div>}</section>
            <section className="inspector-section session-section"><div className="section-heading"><h3>DEMO ENVIRONMENT</h3><span className="status-dot"/></div><dl className="memory-list"><div><dt>Model connection</dt><dd>{modelConfigured ? 'Configured' : 'Not configured'}</dd></div><div><dt>Business date</dt><dd>{state ? state.demo_date : '—'}</dd></div><div><dt>Email delivery</dt><dd>Simulated only</dd></div><div><dt>Session</dt><dd className="mono">{snapshot ? snapshot.session_id.slice(0, 12) : 'Connecting…'}</dd></div></dl><p className="muted-note">The demo date keeps fixture deadlines reproducible.</p></section>
          </> : <section className="inspector-section activity-section"><div className="section-heading"><h3>WORKFLOW EVENTS</h3><span className="badge neutral">SERVER RECORDED</span></div><p className="muted-note">A concise record of decisions and tool activity. No model chain of thought is displayed.</p>{!events.length && <p className="empty-events">Events will appear as the conversation progresses.</p>}<ol className="event-list">{events.slice().reverse().map((event, i) => <li key={`${event.at}-${i}`}><span className="event-dot"/><div><div className="event-title"><strong>{display(event.kind)}</strong><time>{new Date(event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time></div><p>{display(event.detail)}</p><small>{event.phase}</small></div></li>)}</ol></section>}
        </div>
        <div className="inspector-bottom"><Icon name="shield" size={15}/><span>Natural language. Explicit boundaries.</span></div>
      </aside>
    </main>
    <dialog ref={dialog} className="settings-dialog" onCancel={e => { e.preventDefault(); closeSettings(); }} onClick={e => { if (e.target === e.currentTarget) closeSettings(); }}>
      <div className="dialog-content"><div className="dialog-header"><span className="settings-icon"><Icon name="key" size={23}/></span><button className="icon-button" onClick={closeSettings} aria-label="Close model settings" disabled={modelBusy}><Icon name="close"/></button></div><div className="eyebrow">YOUR MODEL, YOUR WORKSPACE</div><h2>Connect the conversation.</h2><p className="dialog-intro">Configure an OpenAI-compatible or Anthropic API to start chatting. Your session uses the model you choose.</p>
        <div className="settings-fields"><label>API protocol<select value={apiProtocol} onChange={e => changeProtocol(e.target.value as ApiProtocol)} disabled={modelBusy}><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic (Claude)</option></select></label><label>API base URL{config?.hosted ? <select value={baseUrl} onChange={e => setBaseUrl(e.target.value)} disabled={modelBusy}>{config.allowed_model_base_urls!.map(url => <option key={url} value={url}>{url}</option>)}</select> : <input type="url" value={baseUrl} placeholder="Enter your provider’s API base URL" onChange={e => setBaseUrl(e.target.value)} autoComplete="off" disabled={modelBusy}/>}<small>{config?.hosted ? 'Choose an endpoint supported by this hosted demo. A different protocol or endpoint requires your own key.' : 'Use the API base address, including /v1 when required. A different protocol or endpoint requires your own key.'}</small></label><label>Model name<input value={model} placeholder="Enter a model supported by your provider" onChange={e => setModel(e.target.value)} autoComplete="off" disabled={modelBusy}/></label><label>API key <span className="optional">{config?.configured && apiProtocol === config.api_protocol ? 'Server default available' : 'Your provider’s API key'}</span><input type="password" value={apiKey} placeholder="Paste your API key" onChange={e => setApiKey(e.target.value)} autoComplete="off" spellCheck={false} disabled={modelBusy}/></label><div className="privacy-note"><Icon name="shield" size={17}/><p>{config?.hosted ? 'Your key is sent over HTTPS to this hosted server and stored only in server memory; the connection expires after one hour. It is not persisted. Using your own key bills model API usage to your provider account. The key is cleared from this form when applied or closed and is never saved in browser storage.' : 'Your key is submitted to the backend and cleared from this form when applied or closed. It is never saved in browser storage.'} Relevant conversation context is sent to your model provider.</p></div></div>
        {modelError && <div className="error-banner" role="alert">{modelError}</div>}{modelMessage && <div className="notice" role="status">{modelMessage}</div>}
        <div className="dialog-footer">{modelConfigured && <button className="button subtle disconnect-button" disabled={modelBusy} onClick={disconnectModel}>Disconnect</button>}<button className="button subtle" disabled={modelBusy} onClick={() => modelAction(true)}><Icon name="refresh" size={16}/>Test connection</button><button className="button primary" disabled={modelBusy} onClick={() => modelAction()}>{modelBusy ? 'Working…' : 'Apply model'}<Icon name="arrow" size={17}/></button></div>
      </div>
    </dialog>
  </div>;
}
