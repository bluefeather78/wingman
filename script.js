// Opportunities database is loaded asynchronously from opportunities.json.
let OPPORTUNITIES = [];
fetch('opportunities.json')
  .then(res => res.json())
  .then(data => { OPPORTUNITIES = Array.isArray(data) ? data : []; })
  .catch(err => console.error('Failed to load opportunities.json:', err));

// ============================================================
// Auth — plain userid/password sign-in + registration. Accounts are persisted
// server-side in a JSON file database (see server.py: /api/register, /api/login,
// users_db.json) so an account, once created, survives page reloads and works
// from any browser hitting this server — not just a client-side cache.
// Passwords are hashed with SHA-256 client-side before ever leaving the browser;
// the server only ever sees/stores the hash. Reasonable for a prototype, but not
// production-grade (no salting, no HTTPS enforcement, no rate limiting).
// ============================================================

let currentUser = null; // { userid, firstName, lastName, email } — the signed-in session, cached locally

// ============================================================
// Persistence layer for profile/tracker data. Prefers window.storage when the
// hosting runtime provides it (e.g. the Claude.ai artifact preview); otherwise
// falls back to server-side per-account storage via /api/data/*, scoped to the
// signed-in user's ID. Without this fallback, none of it survives logout/login
// or a page reload when just running `python server.py` in a plain browser tab
// — window.storage doesn't exist there, so every get/set silently no-ops.
// Mirrors window.storage's { value } get / (key, jsonString) set shape so
// existing call sites barely change.
// ============================================================
const AppStorage = {
  async get(key){
    if(window.storage){
      try{ return await window.storage.get(key); }catch(e){ return null; }
    }
    if(!currentUser || !currentUser.userid) return null;
    try{
      const res = await fetch('/api/data/load', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userid: currentUser.userid, key })
      });
      if(!res.ok) return null;
      const data = await res.json();
      return (data && data.value !== undefined && data.value !== null) ? { value: JSON.stringify(data.value) } : null;
    }catch(e){ return null; }
  },
  async set(key, value){
    if(window.storage){
      try{ await window.storage.set(key, value); }catch(e){}
      return;
    }
    if(!currentUser || !currentUser.userid) return;
    try{
      await fetch('/api/data/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ userid: currentUser.userid, key, value: JSON.parse(value) })
      });
    }catch(e){ /* best-effort — worst case this save is lost */ }
  }
};

// Loads everything scoped to the signed-in account (profile, tracker items, saved
// state) and re-renders. Called on every successful login/register — see showApp().
async function loadAccountData(){
  await Promise.all([loadProfile(), loadTrackerData(), loadTrackerSaved()]);
  renderProfile();
  renderSuggestEntryCard();
}

async function loadUser(){
  try{
    if(window.storage){
      const r = await window.storage.get('hs-user');
      if(r && r.value){ currentUser = JSON.parse(r.value); }
    }
  }catch(e){ /* nothing saved yet, or storage unavailable */ }
}
async function saveUser(){
  try{ if(window.storage) await window.storage.set('hs-user', JSON.stringify(currentUser)); }
  catch(e){ /* storage unavailable — stays in-memory only for this session */ }
}

// Hashes a password with SHA-256 and returns it as a hex string.
async function hashPassword(password){
  const data = new TextEncoder().encode(password);
  const digest = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('');
}

// Toggles between the Sign In and Register forms on the login screen.
function showLoginMode(mode){
  const signInForm = document.getElementById('signInForm');
  const registerForm = document.getElementById('registerForm');
  const tagline = document.getElementById('loginTagline');
  const signInError = document.getElementById('signInError');
  const registerError = document.getElementById('registerError');
  if(signInError) signInError.textContent = '';
  if(registerError) registerError.textContent = '';
  if(mode === 'register'){
    if(signInForm) signInForm.classList.add('hidden');
    if(registerForm) registerForm.classList.remove('hidden');
    if(tagline) tagline.textContent = 'Create an account to find and track opportunities built around your projects.';
  }else{
    if(registerForm) registerForm.classList.add('hidden');
    if(signInForm) signInForm.classList.remove('hidden');
    if(tagline) tagline.textContent = 'Sign in to find and track opportunities built around your projects.';
  }
}

async function registerUser(event){
  event.preventDefault();
  const errorEl = document.getElementById('registerError');
  const firstName = document.getElementById('regFirstName').value.trim();
  const lastName = document.getElementById('regLastName').value.trim();
  const email = document.getElementById('regEmail').value.trim();
  const userid = document.getElementById('regUserid').value.trim();
  const password = document.getElementById('regPassword').value;
  const passwordConfirm = document.getElementById('regPasswordConfirm').value;

  if(!firstName || !lastName || !email || !userid || !password || !passwordConfirm){
    if(errorEl) errorEl.textContent = 'Please fill in every field.';
    return;
  }
  if(password.length < 8){
    if(errorEl) errorEl.textContent = 'Password must be at least 8 characters.';
    return;
  }
  if(password !== passwordConfirm){
    if(errorEl) errorEl.textContent = 'Passwords do not match.';
    return;
  }

  const passwordHash = await hashPassword(password);
  let data;
  try{
    const res = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ firstName, lastName, email, userid, passwordHash })
    });
    data = await res.json().catch(() => ({}));
    if(!res.ok){
      if(errorEl) errorEl.textContent = data.error || 'Could not create account.';
      return;
    }
  }catch(e){
    if(errorEl) errorEl.textContent = 'Could not reach the server. Please try again.';
    return;
  }

  currentUser = { userid, firstName, lastName, email };
  await saveUser();
  await showApp();
}

async function loginUser(event){
  event.preventDefault();
  const errorEl = document.getElementById('signInError');
  const userid = document.getElementById('signInUserid').value.trim();
  const password = document.getElementById('signInPassword').value;

  if(!userid || !password){
    if(errorEl) errorEl.textContent = 'Please enter your user ID and password.';
    return;
  }

  const passwordHash = await hashPassword(password);
  let data;
  try{
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid, passwordHash })
    });
    data = await res.json().catch(() => ({}));
    if(!res.ok){
      if(errorEl) errorEl.textContent = data.error || 'Could not sign in.';
      return;
    }
  }catch(e){
    if(errorEl) errorEl.textContent = 'Could not reach the server. Please try again.';
    return;
  }

  currentUser = { userid, firstName: data.firstName, lastName: data.lastName, email: data.email };
  await saveUser();
  await showApp();
}

function showLoginGate(){
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  if(loginPage) loginPage.classList.remove('hidden');
  if(appShell) appShell.classList.add('hidden');
  showLoginMode('signin');
}
async function showApp(){
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  if(loginPage) loginPage.classList.add('hidden');
  if(appShell) appShell.classList.remove('hidden');

  const nameEl = document.getElementById('accountName');
  const emailEl = document.getElementById('accountEmail');
  const greetingEl = document.getElementById('homeGreetingName');
  const fullName = [currentUser.firstName, currentUser.lastName].filter(Boolean).join(' ');
  if(nameEl) nameEl.textContent = fullName || currentUser.email;
  if(emailEl) emailEl.textContent = currentUser.email || '';
  if(greetingEl) greetingEl.textContent = currentUser.firstName || 'there';

  // Profile/tracker data is scoped to this account (see AppStorage) — load it fresh
  // on every sign-in rather than trusting whatever's still sitting in memory from a
  // previous session in this tab.
  await loadAccountData();
  showPage('home');
}
async function logoutUser(){
  currentUser = null;
  await saveUser();
  // Clear in-memory app data so it can't leak into a different account that signs
  // in next in this same tab — the next login re-fetches everything fresh via
  // loadAccountData().
  studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0 };
  trackerData = { summerPrograms: [], internships: [], researchCompetitions: [], pureCompetitions: [], conferences: [], journals: [] };
  trackerSavedState = {};
  toggleProfile(); // close the drawer on the way out
  showLoginGate();
}

// ============================================================
// Passion Project Opportunity Finder — core logic
// ============================================================

// ---------- Kind configuration (Stage 0 choice drives Stage 1's form) ----------
const KIND_CONFIG = {
  summer: {
    name: 'Summer Program',
    desc: 'Camps, pre-college programs, and summer academies',
    source: 'local',
    dbTypes: ['Program'],
    heading: 'What are your interests?',
    sub: 'Tell us what excites you — subjects, hobbies, activities, or things you\'d love to explore. The more specific, the better the matches.',
    label: 'What are you interested in?',
    placeholder: 'e.g. I love robotics and want to get hands-on with building and programming robots. I\'m also curious about biology, especially genetics, and I enjoy creative writing on the side...'
  },
  internship: {
    name: 'Internship',
    desc: 'Hands-on positions with mentors, labs, or organizations',
    source: 'local',
    dbTypes: ['Internship'],
    heading: 'What are your interests and what kind of experience are you looking for?',
    sub: 'Tell us the field you want to work in, any relevant skills or coursework you already have, and what kind of hands-on experience you\'re hoping to gain.',
    label: 'Your interests and target experience',
    placeholder: 'e.g. I\'m interested in biomedical research, especially cancer biology. I\'ve taken AP Biology and Chemistry and done independent reading on immunotherapy. I\'m looking for a lab position where I can get real hands-on research experience...'
  },
  conference: {
    name: 'Conference Venue',
    desc: 'Academic workshops and conferences to submit a paper to',
    source: 'web',
    comingSoon: true,
    venueKind: 'academic conferences or workshops that review and present papers',
    heading: 'Describe your research',
    sub: 'Tell us what your research is about, the methods or approach you used, and what stage it\'s at (early idea, in progress, or a finished paper ready to submit).',
    label: 'Describe your research',
    placeholder: 'e.g. My research investigates whether large language models encode Hindi grammatical case roles (kāraka) independently of surface case marking. I use linear probing and LEACE causal concept erasure on mBERT, HindBERT, and MuRIL...'
  },
  journal: {
    name: 'Journal Venue',
    desc: 'Academic and student journals to publish a paper in',
    source: 'web',
    comingSoon: true,
    venueKind: 'academic or student research journals that accept manuscript submissions',
    heading: 'Describe your research',
    sub: 'Tell us what your research is about, the methods or approach you used, and what stage it\'s at (early idea, in progress, or a finished paper ready to submit).',
    label: 'Describe your research',
    placeholder: 'e.g. My research develops a grapheme-to-phoneme system for three endangered Finnic languages — Karelian, Livonian, and Ingrian — comparing rule-based and neural approaches...'
  },
  'research-competition': {
    name: 'Research or Project Competition',
    desc: 'Science fairs, app challenges, and project-based contests',
    source: 'local',
    dbTypes: ['Competition','Research'],
    heading: 'Describe your project',
    sub: 'Tell us what you\'ve built or researched, the techniques or skills involved, and what makes it worth entering into a competition.',
    label: 'Describe your project',
    placeholder: 'e.g. I built an AI-powered app that helps autistic children practice reading comprehension, using a speech recognition model fine-tuned on atypical speech and a visual system that shows images and asks kids questions about them out loud...'
  },
  'pure-competition': {
    name: 'Academic Competition',
    desc: 'Skills or knowledge tests — olympiads, quiz bowls, exams',
    source: 'local',
    dbTypes: ['Competition'],
    heading: 'Describe your interests and skill level',
    sub: 'Tell us the subject or skill area, your current level of experience, and what kind of challenge you\'re looking for.',
    label: 'Your interests and skill level',
    placeholder: 'e.g. I\'m strong in math and really enjoy olympiad-style problem solving — number theory and combinatorics especially. I\'ve done well in local math club competitions and want to push myself further with regional or national-level contests...'
  }
};

let selectedKind = null;

function renderKindGrid(){
  const grid = document.getElementById('kindGrid');
  const keys = Object.keys(KIND_CONFIG);
  // Active kinds first, "Coming soon" kinds pushed to the end.
  const ordered = [...keys.filter(k => !KIND_CONFIG[k].comingSoon), ...keys.filter(k => KIND_CONFIG[k].comingSoon)];
  grid.innerHTML = ordered.map(key => {
    const c = KIND_CONFIG[key];
    if(c.comingSoon){
      return `
        <div class="pop-card bg-slate-50 border-2 border-slate-300 p-4 rounded-2xl opacity-60 text-left">
          <div class="flex justify-between items-start gap-2">
            <span class="font-heading font-bold text-slate-500">${c.name}</span>
            <span class="bg-amber-100 text-amber-800 font-bold text-[10px] uppercase px-2 py-0.5 rounded-full border border-amber-800">Soon</span>
          </div>
          <p class="text-xs text-slate-400 mt-1">${c.desc}</p>
        </div>
      `;
    }
    return `
      <button class="pop-card bg-white p-4 rounded-2xl hover:bg-slate-50 text-left w-full transition-colors" onclick="selectKind('${key}')">
        <span class="block font-heading font-bold text-slate-900">${c.name}</span>
        <span class="block text-xs text-slate-500 mt-1">${c.desc}</span>
      </button>
    `;
  }).join('');
}
renderKindGrid();

function selectKind(kind){
  const cfg = KIND_CONFIG[kind];
  if(!cfg || cfg.comingSoon) return;
  selectedKind = kind;
  document.getElementById('stage1Heading').firstChild.textContent = cfg.heading + ' ';
  document.getElementById('stage1Label').textContent = cfg.label;
  const box = document.getElementById('pInterests');
  box.placeholder = cfg.placeholder;
  // Browsing by type is a deliberately from-scratch search — unlike "Suggest
  // opportunities for me", it must never be prepopulated with the saved profile.
  box.value = '';
  const recallNote = document.getElementById('stage1RecallNote');
  if(recallNote) recallNote.classList.remove('show');
  const len = box.value.length;
  charCountEl.textContent = `${len} characters` + (len < 200 ? ' — aim for at least 200' : '');
  document.getElementById('prefsRow').style.display = cfg.source === 'web' ? 'none' : 'grid';
  document.getElementById('formError').classList.remove('show');
  unlocked[1] = true;
  goStage(1);
}

// ---------- Stage navigation ----------

function goStage(n){
  document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
  document.getElementById('stage-' + n).classList.add('active');
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
const unlocked = { 0: true, 1: false, 2: false };

// ---------- "Not sure?" branching quiz ----------
const QUIZ_BRANCHES = {
  project: {
    question: 'What would you like to do with it?',
    options: [
      { kind:'research-competition', title:'Enter it in a competition', desc:'Science fairs, app challenges, project-based contests' },
      { kind:'conference', title:'Present it at a conference', desc:'Submit a paper to an academic workshop or conference' },
      { kind:'journal', title:'Get it published', desc:'Submit to an academic or student research journal' }
    ]
  },
  timeoff: {
    question: 'Which sounds more like what you want?',
    options: [
      { kind:'internship', title:'Hands-on work experience', desc:'Work with a lab, company, or organization' },
      { kind:'summer', title:'A camp or enrichment program', desc:'Explore a subject in a structured program' }
    ]
  }
};
function startQuiz(){
  resetQuizToStep1();
  goStage('quiz');
}
function resetQuizToStep1(){
  document.getElementById('quizStep1').style.display = '';
  const step2 = document.getElementById('quizStep2');
  step2.style.display = 'none';
  step2.innerHTML = '';
}
function quizAnswer(value){
  if(value === 'compete'){
    selectKind('pure-competition');
    return;
  }
  const branch = QUIZ_BRANCHES[value];
  if(!branch) return;
  const step2 = document.getElementById('quizStep2');
  step2.innerHTML = `
    <p class="font-heading font-bold text-lg mb-4 quiz-question">${branch.question}</p>
    <div class="space-y-3 quiz-options">
      ${branch.options.map(o => `
        <button class="pop-card w-full text-left p-4 rounded-xl hover:bg-slate-50 quiz-option" onclick="selectKind('${o.kind}')">
          <strong class="block font-heading text-lg">${o.title}</strong>
          <span class="text-sm text-slate-500">${o.desc}</span>
        </button>
      `).join('')}
    </div>
    <button class="text-sm font-bold text-indigo-600 hover:underline mt-4 back-link" onclick="resetQuizToStep1()">← Different answer</button>
  `;
  document.getElementById('quizStep1').style.display = 'none';
  step2.style.display = '';
}

// ---------- Stage 1: character counter ----------
const interestsBox = document.getElementById('pInterests');
const charCountEl = document.getElementById('charCount');
interestsBox.addEventListener('input', () => {
  const len = interestsBox.value.length;
  charCountEl.textContent = `${len} characters` + (len < 200 ? ' — aim for at least 200' : '');
  charCountEl.classList.toggle('warn', false);
});

// ---------- Keyword pre-filter ----------
const STOPWORDS = new Set(['the','a','an','and','or','but','of','to','in','on','for','with','is','are','was','were','be','been','being','it','its','this','that','these','those','i','my','me','we','our','you','your','as','at','by','from','into','about','also','can','will','would','could','should','have','has','had','not','no','so','if','than','then','which','who','what','when','where','how','more','most','some','such','just','like','using','use','used']);

function tokenize(text){
  return (text || '').toLowerCase().match(/[a-z0-9']+/g) || [];
}
function keywordScore(tokens, opp){
  const haystack = (opp.name + ' ' + opp.org + ' ' + opp.summary + ' ' + opp.subject).toLowerCase();
  let score = 0;
  tokens.forEach(t => {
    if(STOPWORDS.has(t) || t.length < 3) return;
    if(haystack.includes(t)) score += 1;
  });
  return score;
}

function preFilter(description, subjectHints, typeFilter){
  const tokens = [...new Set(tokenize(description).filter(t => !STOPWORDS.has(t) && t.length >= 3))];
  const subjSet = new Set((subjectHints || []).map(s => s.toLowerCase()));
  const typeSet = typeFilter && typeFilter.length ? new Set(typeFilter) : null;

  let base = OPPORTUNITIES;
  if(typeSet){
    const byType = OPPORTUNITIES.filter(o => typeSet.has(o.type));
    // Only hard-filter by type if it leaves a reasonable pool — otherwise the
    // Type field for this kind is too sparse to be a useful constraint.
    if(byType.length >= 15){ base = byType; }
  }

  const scored = base.map(opp => {
    let score = keywordScore(tokens, opp);
    if(subjSet.has((opp.subject || '').toLowerCase())) score += 3;
    return { opp, score };
  });
  scored.sort((a, b) => b.score - a.score);
  const withScore = scored.filter(s => s.score > 0);
  const pool = (withScore.length >= 60 ? withScore : scored).slice(0, 180).map(s => s.opp);
  return pool;
}

function slugify(text){
  let base = (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 50);
  if(!base) base = 'opportunity';
  let id = base, n = 2;
  const used = new Set(currentResults.map(r => r.opp.id));
  while(used.has(id)){ id = `${base}-${n}`; n++; }
  return id;
}

// ---------- Claude API helpers ----------
async function callClaude(system, userContent, useWebSearch){
  const body = {
    model: "claude-sonnet-4-6",
    max_tokens: 1000,
    // Each call site's system prompt is fixed, reused verbatim on every request
    // (only userContent varies) — marking it cacheable lets Anthropic skip
    // re-billing/re-processing the full prompt on repeat calls within the TTL.
    system: [ { type: "text", text: system, cache_control: { type: "ephemeral" } } ],
    messages: [ { role: "user", content: userContent } ]
  };
  if(useWebSearch){
    body.tools = [ { type: "web_search_20250305", name: "web_search" } ];
  }
  const res = await fetch("/api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if(!res.ok){ throw new Error(`API error ${res.status}`); }
  const data = await res.json();
  const textBlocks = (data.content || []).filter(b => b.type === "text").map(b => b.text).join("\n");
  const clean = textBlocks.replace(/```json|```/g, "").trim();
  if(!clean){ throw new Error("Empty response from API"); }
  return clean;
}

// Finds the JSON value in a text blob by scanning brace/bracket depth
// (respecting quoted strings and escapes) rather than naive first/last index —
// this survives trailing commentary. If the JSON was cut off mid-generation
// (hit the token limit), it also attempts a best-effort repair by closing
// any still-open strings/arrays/objects before parsing.
function extractJSON(text){
  const start = text.search(/[\{\[]/);
  if(start === -1) throw new Error("No JSON found in response");

  const openChar = text[start];
  const closeChar = openChar === '{' ? '}' : ']';
  let depth = 0;
  let inString = false;
  let escaped = false;
  let end = -1;

  for(let i = start; i < text.length; i++){
    const ch = text[i];
    if(inString){
      if(escaped){ escaped = false; }
      else if(ch === '\\'){ escaped = true; }
      else if(ch === '"'){ inString = false; }
      continue;
    }
    if(ch === '"'){ inString = true; continue; }
    if(ch === '{' || ch === '['){ depth++; }
    else if(ch === '}' || ch === ']'){
      depth--;
      if(depth === 0){ end = i; break; }
    }
  }

  let candidate;
  if(end !== -1){
    candidate = text.slice(start, end + 1);
  }else{
    // Truncated mid-structure — attempt a best-effort repair.
    candidate = text.slice(start);
    if(inString){ candidate += '"'; }
    // Trim a dangling comma or partial key/value before closing.
    candidate = candidate.replace(/,\s*$/, '');
    const stack = [];
    let scanString = false;
    let scanEscaped = false;
    for(let i = 0; i < candidate.length; i++){
      const ch = candidate[i];
      if(scanString){
        if(scanEscaped){ scanEscaped = false; }
        else if(ch === '\\'){ scanEscaped = true; }
        else if(ch === '"'){ scanString = false; }
        continue;
      }
      if(ch === '"'){ scanString = true; continue; }
      if(ch === '{' || ch === '['){ stack.push(ch); }
      else if(ch === '}' || ch === ']'){ stack.pop(); }
    }
    while(stack.length){
      const opener = stack.pop();
      candidate += (opener === '{' ? '}' : ']');
    }
  }

  try{
    return JSON.parse(candidate);
  }catch(e){
    throw new Error(`Could not parse JSON from response: ${e.message}`);
  }
}

const VALID_SUBJECTS = ['Mixed','STEM','Medicine','Humanities','Art','Business','Engineering','Computer Science','Mathematics','Biology','Physics','Astronomy','Chemistry','Leadership','Law','Logic','Education'];

async function inferSubjects(description){
  const system = `You infer which subject categories from a fixed list best match a student's passion-project description. Valid categories (use these exact strings): ${VALID_SUBJECTS.join(', ')}. Respond with ONLY a raw JSON array of 2-5 of the most relevant category strings, no markdown, no preamble. Example: ["Computer Science","STEM","Mathematics"]`;
  const raw = await callClaude(system, description, false);
  const arr = extractJSON(raw);
  return Array.isArray(arr) ? arr.filter(s => VALID_SUBJECTS.includes(s)) : [];
}

async function rankCandidates(description, candidates, prefs){
  const compact = candidates.map(c => ({ id: c.id, name: c.name, org: c.org, summary: c.summary, subject: c.subject, type: c.type, price: c.price, location: c.location, season: c.season }));
  const system = "You are helping a student find the best-fit extracurricular opportunities (programs, internships, competitions, research positions) for their specific passion project, from a candidate list. Read their project description and preferences carefully and select ONLY the opportunities that would genuinely help them grow this specific project, build relevant skills, get recognition for it, or connect with the right community — not just anything thematically adjacent. Leave out weak or generic fits entirely; every opportunity you return must be a genuinely good match. Rank the best 10-12 matches only. For each, write a short specific reason (under 15 words) that names or clearly paraphrases an actual detail from THEIR description/preferences below (a subject, skill, project, goal, or interest they stated) — never write a generic reason that could apply to any student interested in this general field, and never invent details they didn't mention. Assign a tier: 'strong' (excellent, highly specific fit) or 'look' (solid, worth a look). Respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{\"id\":\"...\",\"reason\":\"...\",\"tier\":\"strong|look\"}]. Stay well within a 1000-token response — 10-12 items is a hard cap.";
  const prefsText = prefs ? `\n\nStudent preferences: ${prefs}` : '';
  const userContent = `Student's passion project:\n${description}${prefsText}\n\nCandidate opportunities (JSON):\n${JSON.stringify(compact)}\n\nSelect and rank the best matches per the schema.`;
  const raw = await callClaude(system, userContent, false);
  const arr = extractJSON(raw);
  return Array.isArray(arr) ? arr : [];
}

// ---------- Web-search path (Conference Venue / Journal Venue) ----------
// This dataset is a directory of precollege programs, internships, and
// competitions — it doesn't contain real academic conferences or journals.
// For those two kinds, search the live web instead of the local database.
async function findVenuesViaWeb(description, cfg, prefsText){
  const today = todayLabel();
  const system = `You help a student researcher find real, current ${cfg.venueKind} that fit their specific research. Today's date is ${today}. Use web_search to find and verify actual venues — don't rely only on memorized knowledge, since deadlines and calls-for-papers change. Prefer venues realistically accessible to a high-school or early-career researcher (student research workshops, high-school-friendly journals, open/inclusive workshops), but you can include 1-2 more ambitious or competitive options too.

Screen out discontinued venues: if you find explicit signals a venue is discontinued, paused, or no longer accepting submissions (e.g. "no longer accepting submissions," a dead/404 page, an org site with no trace of it continuing), DO NOT include it in your results at all — skip it and find a real alternative instead.

Date handling: if a venue's listed submission deadline has already passed but it runs on a regular annual/recurring cycle, estimate next cycle's deadline from the prior cycle's timing and set was_estimated to true. Only include a next_deadline_iso when you found or can reasonably estimate one; use null if genuinely unknown. Never invent a date with no basis.

Only include opportunities that are a genuinely good fit — omit weak or generic matches entirely. For each, the "reason" must name or clearly paraphrase an actual detail from the student's research description/preferences below (a topic, method, skill, or goal they stated) — never a generic reason that could apply to any student in this broad field. For each of the best 6-8 matches, respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{\"name\":\"official venue name, include year if known\",\"url\":\"the venue's official URL\",\"org\":\"organizing body, short\",\"summary\":\"under 18 words on scope/format\",\"reason\":\"under 15 words on why it fits THIS research specifically\",\"tier\":\"strong|look\",\"next_deadline_iso\":\"YYYY-MM-DD or null\",\"was_estimated\":true or false}]. Stay well within a 1000-token response — 6-8 items is a hard cap, keep every field short.`;
  const prefsPart = prefsText ? `\nStudent preferences: ${prefsText}` : '';
  const userContent = `Research description:\n${description}${prefsPart}\n\nSearch the web and find the best matching real, current ${cfg.name.toLowerCase()} options.`;
  const raw = await callClaude(system, userContent, true);
  const arr = extractJSON(raw);
  if(!Array.isArray(arr)) return [];
  return arr.map(item => {
    const opp = {
      id: slugify(item.name || item.url || 'venue'),
      name: item.name || 'Untitled venue',
      org: item.org || '',
      summary: item.summary || '',
      url: item.url || '#',
      subject: '',
      type: cfg.name,
      price: '',
      state: '',
      location: '',
      intl: '',
      season: '',
      nextDeadlineISO: item.next_deadline_iso || null,
      wasEstimated: !!item.was_estimated
    };
    return { opp, reason: item.reason || '', tier: ['strong','look'].includes(item.tier) ? item.tier : 'look' };
  });
}

// ============================================================
// Persistent student profile — a single synthesized narrative, not a list
// of individual entries. New information (from the Finder, clarifying
// questions, or a direct edit) is merged into it via an API call that adds,
// updates, or drops details as appropriate, so there is only ever one
// current version, carried across sessions via AppStorage (per-account, see top of file).
// ============================================================
const ACTIVE_KINDS = Object.keys(KIND_CONFIG).filter(k => !KIND_CONFIG[k].comingSoon);

let studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0 };

async function loadProfile(){
  try{
    const result = await AppStorage.get('student-profile');
    if(result && result.value){
      const parsed = JSON.parse(result.value);
      if(typeof parsed.synthesized === 'string'){
        studentProfile.synthesized = parsed.synthesized;
      }else{
        // Migrate the old per-category-entries shape into one readable paragraph,
        // so nothing from before this change is lost. Re-synthesizing properly
        // happens the next time the student adds something via Edit.
        const cats = ['passionProjects','researchInterests','interests'];
        const labels = { passionProjects: 'Passion projects', researchInterests: 'Research interests', interests: 'Interests' };
        const parts = cats
          .filter(c => Array.isArray(parsed[c]) && parsed[c].length)
          .map(c => `${labels[c]}: ` + parsed[c].map(e => e.text).join('; '));
        studentProfile.synthesized = parts.join('. ');
      }
      studentProfile.updatedAt = parsed.updatedAt || null;
      studentProfile.chatRounds = typeof parsed.chatRounds === 'number' ? parsed.chatRounds : 0;
    }
  }catch(e){ /* nothing saved yet, or storage unavailable — start fresh */ }
}
async function saveProfile(){
  try{ await AppStorage.set('student-profile', JSON.stringify(studentProfile)); }
  catch(e){ /* storage unavailable — profile stays in-memory only for this session */ }
}

function toggleProfile(){
  document.getElementById('profilePanel').classList.toggle('translate-x-full');
}
document.addEventListener('click', (e) => {
  const panel = document.getElementById('profilePanel');
  const toggle = document.getElementById('profileToggle');
  if(!panel || !toggle) return;
  // Use composedPath() (captured at dispatch time) instead of .contains(e.target):
  // clicking inside the drawer can re-render its innerHTML synchronously, which
  // detaches the clicked element from the DOM before this bubbled listener runs — making
  // panel.contains(e.target) wrongly report "outside" and slam the drawer shut.
  const path = e.composedPath();
  if(!path.includes(panel) && !path.includes(toggle)){
    panel.classList.add('translate-x-full');
  }
});

// Profile updates are expected periodically as a student's interests/projects evolve —
// past this many days without an update, the Dashboard nudges them to refresh it.
const PROFILE_STALE_DAYS = 14;
function daysSince(iso){
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
}
// Updates the "last updated" badge and staleness nudge banner on the Profile tab's
// summary card. The synthesized text itself, plus editing, lives in renderProfileFit()
// — the Profile tab is the one place the profile is shown and edited in full.
function renderProfile(){
  const updatedEl = document.getElementById('profileUpdated');
  const bannerEl = document.getElementById('profileStaleBanner');
  const hasProfile = !!studentProfile.synthesized;
  const days = studentProfile.updatedAt ? daysSince(studentProfile.updatedAt) : null;
  const isStale = hasProfile && days !== null && days >= PROFILE_STALE_DAYS;

  if(updatedEl){
    const base = 'text-xs font-bold px-3 py-1.5 rounded-full whitespace-nowrap';
    if(!hasProfile){
      updatedEl.textContent = '';
      updatedEl.className = base;
    }else{
      updatedEl.textContent = days === 0 ? 'Updated today' : days === 1 ? 'Updated yesterday' : `Updated ${days} days ago`;
      updatedEl.className = base + ' ' + (isStale ? 'bg-rose-100 text-rose-700' : 'bg-lime-100 text-lime-700');
    }
  }
  if(bannerEl){
    bannerEl.innerHTML = isStale ? `
      <div class="bg-amber-50 border-2 border-amber-400 rounded-2xl p-3 flex flex-wrap items-center justify-between gap-2">
        <p class="text-xs font-bold text-amber-900">It's been ${days} days since you updated your profile — refresh it for the best matches.</p>
        <button class="pop-btn bg-white text-slate-900 font-bold px-3 py-1.5 rounded-xl text-xs shrink-0" onclick="focusProfileChat()">↓ Update via chat</button>
      </div>
    ` : '';
  }
}
// Sends the student to the dedicated Profile tab, where the profile builder lives.
function goToProfile(){
  showPage('profile');
}
// Sends the student to the Profile tab and scrolls/focuses straight into the chat —
// used by every "update my profile" entry point elsewhere in the app (the Finder's
// pre-search prompt, the empty/stale-profile nudges), since the chat is now the only
// way to add, update, or correct anything in the profile.
function goToProfileChat(){
  showPage('profile');
  setTimeout(focusProfileChat, 150);
}
function focusProfileChat(){
  const card = document.getElementById('profileChatCard');
  if(card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const input = document.getElementById('profileChatInput');
  if(input) setTimeout(() => input.focus(), 300);
}

let clearProfileArmed = false;
async function clearProfile(btn){
  if(!clearProfileArmed){
    clearProfileArmed = true;
    const original = btn.textContent;
    btn.textContent = 'Click again to confirm';
    btn.classList.add('confirm-armed');
    setTimeout(() => {
      clearProfileArmed = false;
      btn.textContent = original;
      btn.classList.remove('confirm-armed');
    }, 3000);
    return;
  }
  clearProfileArmed = false;
  studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0 };
  await saveProfile();
  // The in-progress chat session was talking about the profile that just got wiped —
  // start fresh so the next question isn't referencing details that no longer exist.
  profileChatHistory = [];
  profileChatStarters = null;
  profileChatStartersLoading = false;
  renderProfile();
  renderProfileFit();
  renderSuggestEntryCard();
  renderHomeProfileTeaser();
  if(document.getElementById('profileChatMessages')) initProfileChat();
}

// Merges a block of new text into the single synthesized profile via the API — adding,
// updating, or dropping details as the new information warrants — so only one current
// version ever exists. Falls back to a plain append if the API is unavailable, so
// nothing the student wrote is lost even without live access.
async function synthesizeProfile(existing, newText){
  const system = `You maintain a single, coherent running profile of a high school student's academic and extracurricular interests, built up over multiple sessions. You'll be given the student's CURRENT profile (may be empty) and NEW information they just added. Merge the new information in: add genuinely new details, and update or remove anything the new information supersedes or contradicts. Do not drop specific, still-relevant details from the current profile just because they weren't repeated in the new information. Write it as concise statements in FIRST PERSON, as if the student is describing themself (e.g. "I'm interested in...", "I've been working on...", "My goal is..." — not third person, not addressed to the student, not a bulleted list, no markdown). Structure the output as short paragraphs separated by a blank line (double newline). General paragraphs (no prefix) should cover academic interests, extracurriculars, and goals — 1-3 such paragraphs is typical. If the student has described any larger, longer-term "marquee" projects they're personally driving (as opposed to one-off activities or classes), describe EACH one in its OWN separate paragraph prefixed with the literal text "Passion Project: " — one such paragraph per distinct project, never combining multiple projects into one paragraph. Separately, if the student has described any independent research projects (research, papers, studies they're conducting), describe EACH one in its OWN separate paragraph prefixed with the literal text "Research Project: ", same rule — one per project. A project that fits both categories should be listed under whichever one fits best, not both. Only include these prefixed paragraphs for projects actually described — don't fabricate any. Respond with ONLY the updated profile text — no preamble, no quotes around it.`;
  const userContent = `CURRENT PROFILE:\n${existing || '(empty — nothing recorded yet)'}\n\nNEW INFORMATION TO ADD:\n${newText}\n\nRespond with the updated, merged profile text only.`;
  const raw = await callClaude(system, userContent, false);
  return raw.trim();
}
async function mergeIntoProfile(text){
  if(!text || !text.trim()) return;
  try{
    studentProfile.synthesized = await synthesizeProfile(studentProfile.synthesized, text.trim());
  }catch(e){
    console.error('Profile synthesis failed, appending instead:', e);
    studentProfile.synthesized = studentProfile.synthesized ? studentProfile.synthesized + ' ' + text.trim() : text.trim();
  }
  studentProfile.updatedAt = new Date().toISOString();
  await saveProfile();
  renderProfile();
  renderProfileFit();
  renderSuggestEntryCard();
  renderHomeProfileTeaser();
}

// ============================================================
// Profile Builder Chat — a live, playful back-and-forth on the Profile tab that probes
// for details the synthesized profile is missing (or only has shallowly), rather than
// asking the student to write another essay. Each visit escalates in depth via
// studentProfile.chatRounds. The transcript itself is never persisted — only the
// distilled findings, semantically merged into the one running profile when the
// student signals they're done for now (see finishProfileChatSession).
// ============================================================
let profileChatHistory = []; // [{ role: 'bot'|'user', text }], reset each time a session is finished
let profileChatBusy = false;
let profileChatStarters = null; // array of 3 kickoff questions, shown once per fresh session
let profileChatStartersLoading = false;

// Generic fallback starters, used only if the AI call for fresh-session starters fails
// (or times out) — so a flaky connection never leaves the chat stuck with nothing to show.
const FALLBACK_STARTER_QUESTIONS = [
  "If your extracurriculars had a theme song, what would it be — and why does that fit you?",
  "What's something you're weirdly good at that has nothing to do with school?",
  "If you had one free Saturday with zero obligations, what would you actually do with it?"
];

// Races a promise against a plain timeout so a hung network call can never leave the
// chat stuck in a loading state forever — it just falls back instead.
function withTimeout(promise, ms, message){
  return Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(message || 'Timed out')), ms))
  ]);
}

function initProfileChat(){
  if(!document.getElementById('profileChatMessages')) return;
  // Land on the Profile page with no chat in progress? Toss out any stale starters from an
  // earlier visit so we always regenerate fresh ones against the latest profile summary,
  // rather than reusing questions generated before the student's profile last changed.
  if(!profileChatHistory.length && !profileChatStartersLoading){
    profileChatStarters = null;
  }
  renderProfileChatMessages();
  if(!profileChatHistory.length && !profileChatStarters && !profileChatStartersLoading){
    loadProfileChatStarters();
  }
}

function renderProfileChatMessages(){
  const wrap = document.getElementById('profileChatMessages');
  if(!wrap) return;

  if(profileChatHistory.length){
    wrap.innerHTML = profileChatHistory.map(m =>
      `<div class="${m.role === 'bot' ? 'chat-bubble-bot' : 'chat-bubble-user'}">${escapeHtmlTracker(m.text)}</div>`
    ).join('') + (profileChatBusy ? `<div class="chat-bubble-bot text-slate-400">…</div>` : '');
    wrap.scrollTop = wrap.scrollHeight;
    return;
  }

  // Fresh session, no messages yet — offer 3 starter questions to choose from instead of
  // just launching into one, per the "ask the user where they want to start" behavior.
  if(profileChatStarters && profileChatStarters.length){
    wrap.innerHTML = `
      <div class="flex items-center justify-between gap-2 mb-1">
        <p class="text-xs font-bold text-slate-500">Pick a place to start:</p>
        <button type="button" class="text-xs font-bold text-indigo-600 hover:underline disabled:opacity-50 disabled:cursor-not-allowed shrink-0" onclick="regenerateProfileChatStarters()" ${profileChatStartersLoading ? 'disabled' : ''}>${profileChatStartersLoading ? 'Regenerating…' : '🔄 Regenerate'}</button>
      </div>
      ${profileChatStarters.map((q, i) => `
        <button type="button" class="chat-starter-btn" ${profileChatStartersLoading ? 'disabled' : ''} onclick="pickProfileChatStarter(${i})">${escapeHtmlTracker(q)}</button>
      `).join('')}
    `;
    return;
  }

  wrap.innerHTML = `<p class="empty-state">Cooking up a few conversation starters…</p>`;
}

// Fetches 3 fresh, profile-aware icebreakers to kick off a brand-new chat session.
// `regenerate` — set when the student clicked "Regenerate" on an already-loaded set of
// starters (see regenerateProfileChatStarters) — swaps in a directive that explicitly
// prioritizes breadth (new, untouched areas of their life) over depth (drilling further
// into interests the profile already covers well).
async function profileChatStarterQuestionsFromAI(regenerate){
  const breadthDirective = regenerate ? ` The student explicitly asked to regenerate these — swap in a fresh set. Prioritize BREADTH over depth: favor surfacing entirely new areas of their life the profile hasn't touched at all (academics, social life, jobs, family, random obsessions, sports, art, gaming, etc.) over drilling further into what's already well-covered. Where a question does build on something they've already mentioned, use it only as a springboard to go one layer deeper on that specific thing — but most of the three should open up completely uncovered territory rather than deepen existing ones.` : '';
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY (may be empty). Come up with exactly THREE distinct, short, fun, wacky-but-meaningful icebreaker questions to kick off a chat session that probes for details the profile is missing or only has shallowly — think music, sports/athletics, hobbies, what they do purely for fun, leadership, part-time jobs, quirks of personality, or deeper specifics on things already mentioned.${breadthDirective} Keep each one playful and casual, like a clever friend riffing with them, not a form — but each must serve a real purpose in understanding this student for extracurricular/college-application matching. This is chat round ${studentProfile.chatRounds + 1} of them returning to this page — the higher that number, the more specific and creative the questions should get. Respond with ONLY a JSON array of exactly 3 short question strings, e.g. ["...", "...", "..."] — no markdown, no preamble, no numbering.`;
  const userContent = `CURRENT PROFILE SUMMARY:\n${studentProfile.synthesized || '(empty)'}\n\nRespond with a JSON array of exactly 3 starter questions only.`;
  const raw = await withTimeout(callClaude(system, userContent, false), 20000, 'Timed out waiting for starter questions');
  const parsed = extractJSON(raw);
  if(!Array.isArray(parsed) || !parsed.length) throw new Error('Unexpected starter question format');
  return parsed.slice(0, 3).map(String);
}

async function loadProfileChatStarters(){
  profileChatStartersLoading = true;
  renderProfileChatMessages();
  try{
    profileChatStarters = await profileChatStarterQuestionsFromAI();
  }catch(e){
    console.error('Profile chat starters failed, using fallback:', e);
    profileChatStarters = FALLBACK_STARTER_QUESTIONS.slice();
  }
  profileChatStartersLoading = false;
  renderProfileChatMessages();
}

// Student clicked "Regenerate" on an already-loaded set of starters — keeps the old set
// visible (disabled) while fetching, so a slow/flaky call doesn't blank the panel, and
// leaves the old set in place on failure rather than losing them.
async function regenerateProfileChatStarters(){
  if(profileChatStartersLoading) return;
  profileChatStartersLoading = true;
  renderProfileChatMessages();
  try{
    profileChatStarters = await profileChatStarterQuestionsFromAI(true);
  }catch(e){
    console.error('Regenerating profile chat starters failed, keeping previous set:', e);
  }
  profileChatStartersLoading = false;
  renderProfileChatMessages();
}

// The student picked one of the 3 opening options — that becomes the bot's first message,
// and the conversation proceeds normally (free text answer, then follow-up questions).
function pickProfileChatStarter(i){
  const q = profileChatStarters && profileChatStarters[i];
  if(!q) return;
  profileChatHistory.push({ role: 'bot', text: q });
  profileChatStarters = null;
  renderProfileChatMessages();
  const input = document.getElementById('profileChatInput');
  if(input) input.focus();
}

// Calls Claude for the bot's next question, given the profile-so-far, the transcript so
// far, and how many prior chat rounds (visits) this student has already completed —
// deeper rounds are prompted to dig further/get weirder instead of repeating ground.
async function profileChatNextQuestion(){
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY (may be empty) and the CONVERSATION SO FAR in this session. Ask exactly ONE short, fun, wacky-but-meaningful question that helps fill in gaps — especially topics not yet covered, like music, sports/athletics, hobbies, what they do purely for fun, family or community involvement, leadership moments, part-time jobs, or quirks of personality — as well as digging deeper into things already mentioned (ask for specifics: what exactly did you build, what was your role, what surprised you, what would you change). This is chat round ${studentProfile.chatRounds + 1} of them returning to this page — the more rounds, the more specific and creative your questions should get; don't repeat ground already covered in earlier rounds or earlier in this conversation. Keep your tone playful and casual, like a clever friend riffing with them, not a form — but every question must serve a real purpose in understanding this student for extracurricular/college-application matching. Ask exactly one question, 1-2 sentences, no lists, no markdown, no preamble, no "Great!" acknowledgments of their last answer beyond at most a short playful reaction folded into the same sentence.`;
  const transcript = profileChatHistory.map(m => `${m.role === 'bot' ? 'You' : 'Student'}: ${m.text}`).join('\n') || '(nothing yet)';
  const userContent = `CURRENT PROFILE SUMMARY:\n${studentProfile.synthesized || '(empty)'}\n\nCONVERSATION SO FAR:\n${transcript}\n\nRespond with your next single question only — no preamble, no quotes around it.`;
  const raw = await withTimeout(callClaude(system, userContent, false), 20000, 'Timed out waiting for the next question');
  return raw.trim();
}

async function sendProfileChatBotTurn(){
  profileChatBusy = true;
  renderProfileChatMessages();
  try{
    const question = await profileChatNextQuestion();
    profileChatHistory.push({ role: 'bot', text: question || "What's something you're into that might surprise people?" });
  }catch(e){
    console.error('Profile chat question failed:', e);
    profileChatHistory.push({ role: 'bot', text: "Hmm, I couldn't think of a question just now — want to just tell me something about yourself?" });
  }
  profileChatBusy = false;
  renderProfileChatMessages();
}

async function sendProfileChatMessage(){
  if(profileChatBusy) return;
  const input = document.getElementById('profileChatInput');
  const text = input ? input.value.trim() : '';
  if(!text) return;
  profileChatHistory.push({ role: 'user', text });
  if(input) input.value = '';
  renderProfileChatMessages();
  await sendProfileChatBotTurn();
}

// Distills the chat transcript into plain findings text, then folds it into the single
// running profile via the same semantic merge used everywhere else (mergeIntoProfile).
async function summarizeProfileChat(){
  const system = `You help distill a casual chat conversation into new facts learned about a high school student, so they can be merged into their profile. Given the CONVERSATION, extract only the new, concrete details the student actually shared — interests, activities, projects, personality, hobbies, and so on. Ignore the chatbot's own questions and any small talk. Write it as a few short first-person-compatible factual notes (plain text, no markdown, no preamble, no bullet points) describing what was learned, ready to be merged into a first-person profile summary.`;
  const transcript = profileChatHistory.map(m => `${m.role === 'bot' ? 'Bot' : 'Student'}: ${m.text}`).join('\n');
  const userContent = `CONVERSATION:\n${transcript}\n\nRespond with the distilled findings only.`;
  const raw = await callClaude(system, userContent, false);
  return raw.trim();
}

async function finishProfileChatSession(){
  const statusEl = document.getElementById('profileChatStatus');
  const hasAnswers = profileChatHistory.some(m => m.role === 'user');
  if(!hasAnswers){
    if(statusEl) statusEl.textContent = 'Answer at least one question first, then hit this again.';
    return;
  }
  if(statusEl) statusEl.textContent = 'Folding what you shared into your profile…';
  try{
    const findings = await summarizeProfileChat();
    await mergeIntoProfile(findings);
    studentProfile.chatRounds += 1;
    await saveProfile();
    profileChatHistory = [];
    profileChatStarters = null;
    profileChatStartersLoading = false;
    renderProfileChatMessages();
    loadProfileChatStarters();
    if(statusEl) statusEl.textContent = 'Profile updated! Come back any time for more questions.';
  }catch(e){
    console.error('Profile chat summarize/merge failed:', e);
    if(statusEl) statusEl.textContent = "Couldn't update your profile just now — try again in a moment.";
  }
}


// Used by empty-profile prompts elsewhere in the app to send the student to the one
// place the profile now lives: the Profile tab.
// ============================================================
// "Suggest opportunities for me" — profile-based discovery. Skips straight
// to matching using the synthesized profile, asking clarifying questions
// first if it isn't specific enough yet. The readiness check also decides
// which live opportunity kinds are relevant (excludes comingSoon kinds
// like Conference/Journal Venue) — there's no fixed category mapping to
// fall back on now that the profile is a single blob, not per-category lists.
// ============================================================

// Renders the "Suggest opportunities for me" entry card on stage 0. Disabled with a
// prompt to update the profile when there's nothing to work with yet — otherwise active.
function renderSuggestEntryCard(){
  const el = document.getElementById('suggestEntryCard');
  if(!el) return;
  if(!studentProfile.synthesized){
    el.innerHTML = `
      <div class="max-w-xl">
        <h2 class="font-heading font-extrabold text-3xl mb-3">Suggest opportunities for me</h2>
        <p class="text-sm text-slate-600">Based on everything in your profile. Add a few things to your profile first and this option unlocks.</p>
      </div>
      <button class="mt-6 pop-btn bg-orange-500 text-slate-900 font-bold px-6 py-3 rounded-xl" onclick="goToProfileChat()">Go to Your Profile →</button>
    `;
    return;
  }
  const preview = studentProfile.synthesized.length > 160 ? studentProfile.synthesized.slice(0, 160) + '…' : studentProfile.synthesized;
  el.innerHTML = `
    <div class="max-w-xl">
      <h2 class="font-heading font-extrabold text-3xl mb-3">Suggest opportunities for me</h2>
      <p class="text-sm text-slate-600 mb-3">Skip straight to matches based on your profile — the fastest way to get started.</p>
      <p class="text-xs text-slate-500 font-medium italic border-l-2 border-slate-300 pl-3">"${escapeHtmlTracker(preview)}"</p>
    </div>
    <button class="mt-6 pop-btn bg-orange-500 text-slate-900 font-bold px-6 py-3 rounded-xl" onclick="startProfileSuggest()">Suggest opportunities →</button>
  `;
}
function toggleBrowsePanel(){
  const panel = document.getElementById('browsePanel');
  const btn = document.getElementById('browseToggleBtn');
  if(!panel) return;
  const willOpen = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !willOpen);
  if(btn) btn.textContent = willOpen ? 'Hide opportunity types ↑' : 'Prefer to browse opportunities? Click here';
  if(willOpen) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

let suggestPendingQuestions = [];
let suggestAssessedKinds = [];
// Read-only preview of the synthesized profile on the Finder's "Here's what we know
// about you" card. Editing no longer happens in place here — the profile is only ever
// changed via the chat on the Profile tab (see goToProfileChat).
function renderSuggestProfileSummary(){
  const el = document.getElementById('suggestProfileSummary');
  if(!el) return;
  el.innerHTML = `<div class="bg-indigo-50 border-2 border-slate-900 rounded-2xl p-4 sm:p-6">${profileSummaryBodyHTML(studentProfile.synthesized)}</div>`;
}
async function startProfileSuggest(){
  if(!studentProfile.synthesized) return;
  goStage('suggest');
  renderSuggestProfileSummary();
  document.getElementById('suggestQuestionsWrap').innerHTML = '';
  document.getElementById('suggestContinueBtn').style.display = 'none';
  document.getElementById('suggestError').classList.remove('show');
  // Give the student a chance to review/update their profile before we spend an API
  // call and lock in a search against it — see confirmSuggestProfile() for the
  // actual readiness-check + search, which only fires once they confirm.
  document.getElementById('suggestConfirmWrap').style.display = '';
  document.getElementById('suggestStatus').textContent = '';
}
async function confirmSuggestProfile(){
  document.getElementById('suggestConfirmWrap').style.display = 'none';
  const statusEl = document.getElementById('suggestStatus');
  statusEl.textContent = 'Reviewing your profile…';
  try{
    const assessment = await assessProfileReadiness(studentProfile.synthesized);
    if(assessment && assessment.ready === false && Array.isArray(assessment.questions) && assessment.questions.length){
      statusEl.textContent = 'A couple quick questions will help narrow this down:';
      renderSuggestQuestions(assessment.questions.slice(0, 3));
    }else{
      suggestAssessedKinds = (assessment && Array.isArray(assessment.kinds) && assessment.kinds.length)
        ? assessment.kinds.filter(k => ACTIVE_KINDS.includes(k))
        : ACTIVE_KINDS.slice();
      statusEl.textContent = '';
      await runProfileSuggestSearch();
    }
  }catch(err){
    console.error('Profile readiness check failed:', err);
    // Graceful fallback — don't block the student on a failed assessment call, search all active kinds.
    suggestAssessedKinds = ACTIVE_KINDS.slice();
    statusEl.textContent = '';
    await runProfileSuggestSearch();
  }
}
async function assessProfileReadiness(profileText){
  const kindList = ACTIVE_KINDS.map(k => `"${k}" (${KIND_CONFIG[k].name}: ${KIND_CONFIG[k].desc})`).join(', ');
  const system = `You help decide whether a student's profile has enough detail to confidently recommend extracurricular opportunities, and which types are relevant. Valid opportunity type keys: ${kindList}. Read the profile below. If it gives clear enough signal about what the student wants to do and why, respond with ONLY raw JSON, no markdown, no preamble: {"ready":true,"kinds":["one or more of the valid type keys, the ones genuinely relevant"]}. If it's too vague, sparse, or ambiguous to match well, respond with ONLY raw JSON matching: {"ready":false,"questions":["a short, specific clarifying question", "..."]}. Ask at most 3 questions, and only ones that would actually change which opportunities fit — don't ask generic questions the profile already answers.`;
  const raw = await callClaude(system, profileText, false);
  return extractJSON(raw);
}
function renderSuggestQuestions(questions){
  suggestPendingQuestions = questions;
  const wrap = document.getElementById('suggestQuestionsWrap');
  wrap.innerHTML = questions.map((q, i) => `
    <label class="field-label" for="suggestQ-${i}">${escapeHtmlTracker(typeof q === 'string' ? q : (q.text || 'Tell us a bit more:'))}</label>
    <textarea id="suggestQ-${i}" rows="2" placeholder="Type your answer…"></textarea>
  `).join('');
  const btn = document.getElementById('suggestContinueBtn');
  btn.style.display = '';
  btn.disabled = false;
  btn.textContent = 'Continue →';
}
async function submitSuggestQuestions(){
  const btn = document.getElementById('suggestContinueBtn');
  btn.disabled = true;
  const answers = [];
  suggestPendingQuestions.forEach((q, i) => {
    const input = document.getElementById('suggestQ-' + i);
    const val = input ? input.value.trim() : '';
    const qText = typeof q === 'string' ? q : (q.text || '');
    if(val) answers.push(`${qText}\nAnswer: ${val}`);
  });
  // All answers are merged into the single synthesized profile in one pass — so they're
  // available for future matching too, not just this one search.
  if(answers.length){
    await mergeIntoProfile(answers.join('\n\n'));
  }
  document.getElementById('suggestQuestionsWrap').innerHTML = '';
  btn.style.display = 'none';
  const statusEl = document.getElementById('suggestStatus');
  statusEl.textContent = 'Reviewing your answers…';
  try{
    const assessment = await assessProfileReadiness(studentProfile.synthesized);
    suggestAssessedKinds = (assessment && Array.isArray(assessment.kinds) && assessment.kinds.length)
      ? assessment.kinds.filter(k => ACTIVE_KINDS.includes(k))
      : ACTIVE_KINDS.slice();
  }catch(e){
    suggestAssessedKinds = ACTIVE_KINDS.slice();
  }
  statusEl.textContent = '';
  await runProfileSuggestSearch();
}
async function runProfileSuggestSearch(){
  const statusEl = document.getElementById('suggestStatus');
  const errorBox = document.getElementById('suggestError');
  errorBox.classList.remove('show');
  const kinds = suggestAssessedKinds.length ? suggestAssessedKinds : ACTIVE_KINDS;
  const description = studentProfile.synthesized;
  if(!description || description.length < 10){
    errorBox.textContent = 'Your profile needs a bit more detail before matching well — add something on the Home page first.';
    errorBox.classList.add('show');
    return;
  }
  try{
    const merged = [];
    for(const kind of kinds){
      const cfg = KIND_CONFIG[kind];
      if(!cfg) continue;
      statusEl.textContent = `Searching ${cfg.name.toLowerCase()}s…`;
      const subjects = await inferSubjects(description);
      let pool = preFilter(description, subjects, cfg.dbTypes);
      if(pool.length < 20){ pool = preFilter(description, subjects, cfg.dbTypes); }
      const ranked = await rankCandidates(description, pool, '');
      const byId = {};
      pool.forEach(o => { byId[o.id] = o; });
      ranked.filter(r => byId[r.id]).forEach(r => {
        merged.push({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','look'].includes(r.tier) ? r.tier : 'look', kind });
      });
    }
    if(!merged.length){
      throw new Error('No matches came back — try adding more detail to your profile, or browse by type instead.');
    }
    currentResults = merged;
    selectedIds = new Set();
    resetResultFilters();
    statusEl.textContent = '';
    renderResults();
    unlocked[2] = true;
    goStage(2);
  }catch(err){
    console.error('Profile-based search failed:', err);
    statusEl.textContent = '';
    errorBox.textContent = `Couldn't complete the search — this only works when the page has live API access (e.g. viewing as a Claude.ai artifact). Error: ${err.message}`;
    errorBox.classList.add('show');
  }
}

// ---------- Stage 1 → 2: run search ----------
let currentResults = []; // [{opp, reason, tier, kind?}] — kind is set for multi-kind Suggest results
let selectedIds = new Set();


async function runSearch(){
  const cfg = KIND_CONFIG[selectedKind];
  const description = interestsBox.value.trim();
  const grade = document.getElementById('pGrade').value;
  const state = document.getElementById('pState').value.trim();
  const priceWant = document.getElementById('pPrice').value;
  const formatWant = document.getElementById('pFormat').value;

  const findBtn = document.getElementById('findBtn');
  const findLabel = document.getElementById('findBtnLabel');
  const status = document.getElementById('formStatus');
  const errorBox = document.getElementById('formError');
  const progressNote = document.getElementById('progressNote');

  errorBox.classList.remove('show');
  if(!cfg){
    errorBox.textContent = 'Pick a type of opportunity first.';
    errorBox.classList.add('show');
    return;
  }
  if(description.length < 30){
    errorBox.textContent = 'Add a bit more detail first — a sentence or two is too little to match well.';
    errorBox.classList.add('show');
    return;
  }

  findBtn.disabled = true;
  findBtn.classList.add('loading');
  findLabel.textContent = 'Reading your description…';
  status.textContent = '';
  progressNote.classList.add('show');
  progressNote.textContent = 'Understanding what you\'re looking for…';

  // Merge this description into the single synthesized profile in the background —
  // doesn't block the search itself.
  mergeIntoProfile(description);

  let prefsParts = [];
  if(grade) prefsParts.push(`grade level: ${grade}`);
  if(state) prefsParts.push(`home state: ${state}`);
  if(cfg.source === 'local'){
    if(priceWant === 'free') prefsParts.push('prefers free opportunities only');
    if(formatWant === 'remote') prefsParts.push('prefers remote-friendly opportunities');
    if(formatWant === 'inperson') prefsParts.push('prefers in-person opportunities');
  }
  // Browsing by type is independent of the saved profile by design (see selectKind) —
  // no profile text is folded into ranking here, unlike the profile-based Suggest flow.
  const prefsText = prefsParts.join('; ');

  try{
    if(cfg.source === 'web'){
      // Conference Venue / Journal Venue — this dataset has no real academic
      // venues, so search the live web directly instead of the local database.
      progressNote.textContent = 'Searching the web for real venues…';
      findLabel.textContent = 'Searching the web…';
      currentResults = await findVenuesViaWeb(description, cfg, prefsText);
    }else{
      const subjects = await inferSubjects(description);
      progressNote.textContent = `Searching ${OPPORTUNITIES.length.toLocaleString()} opportunities…`;
      findLabel.textContent = 'Searching database…';

      let pool = preFilter(description, subjects, cfg.dbTypes);
      if(priceWant === 'free'){ pool = pool.filter(o => o.price === 'Free'); }
      if(formatWant === 'remote'){ pool = pool.filter(o => o.location === 'Remote' || o.location === 'In-Person and Remote'); }
      if(formatWant === 'inperson'){ pool = pool.filter(o => o.location === 'In-Person' || o.location === 'In-Person and Remote'); }
      if(pool.length < 20){
        // fallback: relax filters if too few remain
        pool = preFilter(description, subjects, cfg.dbTypes);
      }

      progressNote.textContent = `Ranking the ${pool.length} closest matches…`;
      findLabel.textContent = 'Ranking best fits…';

      const ranked = await rankCandidates(description, pool, prefsText);
      const byId = {};
      pool.forEach(o => { byId[o.id] = o; });
      currentResults = ranked
        .filter(r => byId[r.id])
        .map(r => ({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','look'].includes(r.tier) ? r.tier : 'look' }));
    }

    if(!currentResults.length){
      throw new Error('No matches came back — try adding more specific detail to your description.');
    }

    // A fresh search is a new "turn" — any selections from a prior search shouldn't
    // carry over into this result set's selected count.
    selectedIds = new Set();
    resetResultFilters();
    renderResults();
    unlocked[2] = true;
    goStage(2);
  }catch(err){
    console.error('Search failed:', err);
    errorBox.textContent = `Couldn't complete the search — this only works when the page has live API access (e.g. viewing as a Claude.ai artifact). Error: ${err.message}`;
    errorBox.classList.add('show');
  }finally{
    findBtn.disabled = false;
    findBtn.classList.remove('loading');
    findLabel.textContent = 'Find matching opportunities';
    progressNote.classList.remove('show');
  }
}

// ---------- Stage 2: render results ----------
const TIER_ORDER = { strong: 0, look: 1 };

function resultCardHTML(r){
  const o = r.opp;
  const isSelected = selectedIds.has(o.id);
  const tracked = findTrackedItem(o);
  const metaParts = [o.org, o.type, o.price, o.location, o.state && o.state !== 'All States' ? o.state : null, o.season].filter(Boolean);
  const kindBadge = r.kind ? KIND_CONFIG[r.kind].name : (o.type || 'Opportunity');
  const bgClass = r.tier === 'strong' ? 'bg-emerald-50' : 'bg-white';
  const dateNote = o.nextDeadlineISO ? `<span class="bg-white border-2 border-indigo-200 text-slate-900 px-3 py-1.5 rounded-full">Next: ${shortDate(o.nextDeadlineISO)}${o.wasEstimated ? ' (est.)' : ''}</span>` : '';
  // Already-tracked opportunities can't be re-selected — clicking "Save Match" on one
  // would just be silently dropped as a duplicate at add time, so instead we surface a
  // tag pointing back to the Tracker, where any edits belong.
  const actionControl = tracked
    ? `<span class="bg-slate-800 text-white font-bold text-xs px-4 py-2 rounded-full cursor-pointer" onclick="event.stopPropagation(); goToTrackerCard('${tracked.item.id}')">📌 Tracking currently. Make edits in tracker.</span>`
    : `<button class="pop-btn font-extrabold text-xs px-5 py-2.5 rounded-full flex items-center justify-center gap-2 border-2 border-slate-900 ${isSelected ? 'bg-lime-400 text-slate-900' : 'bg-white text-slate-900'}" onclick="event.stopPropagation(); toggleSelect('${o.id}')">
            ${isSelected ? '⭐ Saved Match' : '⭐ Save Match'}
         </button>`;

  return `
    <div class="pop-card result-card-clickable ${bgClass} rounded-3xl p-5 sm:p-6 space-y-4 ${isSelected ? 'border-4 border-lime-400 bg-lime-50' : 'border-4 border-slate-900'}" id="result-${o.id}" onclick="window.open('${o.url}', '_blank')">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
         <div class="flex flex-wrap gap-2">
            <span class="bg-violet-200 text-violet-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">${kindBadge}</span>
            ${r.tier === 'strong' ? `<span class="bg-yellow-300 border-2 border-slate-900 font-extrabold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">⭐ Strong Fit</span>` : `<span class="bg-slate-100 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">Worth a look</span>`}
         </div>
         ${actionControl}
      </div>
      <div>
        <h3 class="font-heading text-2xl sm:text-3xl font-extrabold text-slate-900"><a href="${o.url}" target="_blank" class="hover:underline" onclick="event.stopPropagation()">${o.name}</a></h3>
      </div>
      ${r.reason ? `<div class="flex gap-3 items-stretch">
        <div class="w-1 rounded-full bg-yellow-400 shrink-0"></div>
        <div>
          <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Why it fits</p>
          <p class="font-heading text-lg sm:text-xl font-bold text-slate-900 leading-snug">${r.reason}</p>
        </div>
      </div>` : ''}
      <div class="flex flex-wrap gap-2 text-xs font-bold">
         ${metaParts.map(m => `<span class="bg-white border-2 border-indigo-200 text-slate-900 px-3 py-1.5 rounded-full">${m}</span>`).join('')}
         ${dateNote}
      </div>
      ${o.summary ? `<p class="text-sm text-slate-500 font-medium leading-relaxed line-clamp-3">${o.summary}</p>` : ''}
    </div>
  `;
}
// ---------- Result filters (type / cost / season / format) ----------
let resultFilters = { type: new Set(), price: new Set(), location: new Set(), season: new Set() };
let resultVisibleCount = 10;
const RESULT_FILTER_FIELDS = [
  { key: 'type', field: 'type', label: 'Type' },
  { key: 'price', field: 'price', label: 'Cost' },
  { key: 'season', field: 'season', label: 'Season' },
  { key: 'location', field: 'location', label: 'Format' }
];
function resetResultFilters(){
  Object.values(resultFilters).forEach(s => s.clear());
  resultVisibleCount = 10;
}
function filterResultList(list){
  return list.filter(r => RESULT_FILTER_FIELDS.every(f => {
    const set = resultFilters[f.key];
    return !set.size || set.has(r.opp[f.field]);
  }));
}
function renderResultFilterBar(list){
  const wrap = document.getElementById('resultFilterWrap');
  const bar = document.getElementById('resultFilterBar');
  if(!wrap || !bar) return;
  let anyFacet = false;
  bar.innerHTML = RESULT_FILTER_FIELDS.map(f => {
    const values = [...new Set(list.map(r => r.opp[f.field]).filter(Boolean))].sort();
    if(values.length < 2) return '';
    anyFacet = true;
    const panelId = 'resultFilterPanel_' + f.key;
    const activeCount = resultFilters[f.key].size;
    return `
      <div class="relative nav-dropdown">
        <button class="pop-btn bg-white font-bold text-xs px-3 py-2 rounded-xl flex items-center gap-1" onclick="toggleNavDropdownPanel('${panelId}')">
          <span>▾</span> ${f.label}${activeCount ? ` (${activeCount})` : ''}
        </button>
        <div class="absolute left-0 top-full mt-2 w-56 pop-card bg-white p-3 rounded-2xl z-50 hidden nav-dropdown-panel" id="${panelId}">
          <div class="space-y-1">
            ${values.map(v => `
              <label class="flex items-center gap-2 text-xs font-medium py-1 cursor-pointer">
                <input type="checkbox" ${resultFilters[f.key].has(v) ? 'checked' : ''} onchange="toggleResultFilter('${f.key}', this.nextSibling.textContent.trim())">
                ${v}
              </label>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }).join('');
  const anyActive = Object.values(resultFilters).some(s => s.size);
  bar.insertAdjacentHTML('beforeend', anyActive ? `<button class="text-xs font-bold text-indigo-600 hover:underline" onclick="clearResultFilters()">Clear filters</button>` : '');
  wrap.classList.toggle('hidden', !anyFacet);
}
function toggleResultFilter(key, value){
  const set = resultFilters[key];
  if(set.has(value)) set.delete(value); else set.add(value);
  resultVisibleCount = 10;
  renderResults();
}
function clearResultFilters(){
  resetResultFilters();
  renderResults();
}
function showMoreResults(){
  resultVisibleCount += 10;
  renderResults();
}
function renderResults(){
  // Already-tracked opportunities float to the very top, then saved matches (clicking
  // "Save Match" visibly moves a card up into this group), then everything else —
  // each group still ordered by tier internally.
  const resultRank = r => findTrackedItem(r.opp) ? 0 : (selectedIds.has(r.opp.id) ? 1 : 2);
  const sorted = [...currentResults].sort((a, b) => {
    const rankDiff = resultRank(a) - resultRank(b);
    if(rankDiff !== 0) return rankDiff;
    return TIER_ORDER[a.tier] - TIER_ORDER[b.tier];
  });
  renderResultFilterBar(sorted);
  const filtered = filterResultList(sorted);
  const visible = filtered.slice(0, resultVisibleCount);
  document.getElementById('resultGrid').innerHTML = visible.length
    ? visible.map(resultCardHTML).join('')
    : `<p class="empty-state">No matches with these filters. <a href="#" onclick="event.preventDefault(); clearResultFilters();">Clear filters</a></p>`;
  const moreWrap = document.getElementById('resultShowMoreWrap');
  if(moreWrap){
    const remaining = filtered.length - visible.length;
    moreWrap.classList.toggle('hidden', remaining <= 0);
    const btn = document.getElementById('resultShowMoreBtn');
    if(btn) btn.textContent = `Show more (${remaining} left)`;
  }
  updateSelectionBar();
}
function toggleSelect(id){
  if(selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
  renderResults();
}
function updateSelectionBar(){
  const bar = document.getElementById('selectionBar');
  const count = selectedIds.size;
  document.getElementById('selectionCount').textContent = `${count} selected`;
  bar.style.display = currentResults.length ? 'flex' : 'none';
  document.getElementById('buildTrackerBtn').disabled = count === 0;
}

// ---------- Result detail overlay (click a card to expand) ----------
// Locks/unlocks background scrolling while a full-screen modal is open, so the only
// scrollable region on screen is the modal panel itself — avoids the confusing "two
// scrollbars" feel that makes overflowing modal content seem stuck/unscrollable.
function lockBodyScroll(){ document.body.style.overflow = 'hidden'; }
function unlockBodyScroll(){ document.body.style.overflow = ''; }

// ============================================================
// Stage 3: Tracker
// ============================================================
let trackedItems = []; // full card objects with deadline info

function todayLabel(){
  return new Date().toLocaleDateString('en-US', { month:'long', day:'numeric', year:'numeric' });
}
function baseDomain(url){
  try{
    const u = new URL(url);
    return u.protocol + '//' + u.hostname;
  }catch(e){ return url; }
}

async function extractTrackerInfo(opp){
  const today = todayLabel();
  const root = baseDomain(opp.url);
  const system = `You extract structured tracking data for an extracurricular opportunity (program, internship, competition, or research position), for a high-school student's tracker. Today's date is ${today}.

Search thoroughly with web_search:
- Start with the given URL.
- If that page's deadline/cycle information looks stale (from a past year, or missing entirely), also search the organization's base website (e.g. ${root}) for a more current version of this program's page — specific program URLs sometimes point to outdated or archived pages while the org's current site has the live one.
- Look explicitly for language indicating the program is discontinued, paused, cancelled, or not accepting applications this cycle (e.g. "program has ended," "not running this year," "no longer offered"). If you find this, set status to "not_running" and explain briefly in note — do not guess a future deadline for a program you've determined isn't running.

Multiple deadline milestones — this matters a lot:
- Many programs have MORE THAN ONE deadline — e.g. an early-bird/early registration deadline well before a later regular or final deadline (AMC 12's early-bird registration deadline is a good example: it lands weeks before the exam itself). Find and list EVERY distinct deadline milestone you can, each with a short specific label (e.g. "Early Bird Registration", "Regular Registration", "Final Deadline", "Application Deadline", "Late Registration") and its own date, in chronological order. Do not collapse them into just one "final" date — the earliest one is often the one a student needs to act on first.
- If there's genuinely only one deadline, list just that one entry.

Registration/application OPENS date — pay particular, deliberate attention to this:
- Actively search for the date applications/registration OPEN, not just when they close — this is often the single most useful date for a student trying to plan ahead, and it's easy to miss because it's mentioned less prominently than the deadline. Check the program page, past years' timelines, and any "key dates" or "timeline" section specifically for an opens/launch date.
- If you can't find an explicit opens date but the program is recurring, ESTIMATE it from the prior cycle's opens date the same way you'd estimate a deadline (e.g. applications opened January 10 last cycle, program is annual → estimate a similar date this cycle) and set was_estimated true if any part of the dates you're returning is estimated.
- Only leave opens_iso null if you genuinely found no opens date and have no reasonable prior-cycle basis to estimate one — don't skip searching for it just because you already found a deadline.

Date reasoning:
- If every deadline you found has already passed relative to today, and the program appears to run on a regular annual/recurring cycle, ESTIMATE next cycle's dates from the prior cycle's timing (e.g. last deadline was March 15 2025, program is annual → estimate a 2026 date near March 15, or later if you can't confirm the exact next date). Set was_estimated to true and say what it's based on in note (e.g. "Estimated from the 2025 cycle; 2027 dates not yet posted").
- Only mark status "running" if you found real evidence the program is currently active or has a future confirmed/estimated date. Use "unknown" if you found genuinely nothing usable after searching both the URL and the base site.
- Never invent a specific date with no basis — every date must come from something you actually found, whether confirmed or reasonably estimated from a real prior cycle.

Action items — think through what a student would actually need to DO to meet the nearest deadline, not just the deadline itself: e.g. requesting a recommendation letter, drafting an essay, gathering transcripts, preparing a portfolio or writing sample, getting parent/guardian sign-off, registering for a required test. Infer these from the requirements you find and from what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or how to approach its substance, since you have no way of knowing the specifics of their work and must not assume or invent any. List 3-5 short, concrete action items (skip this if status is not_running).

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching exactly this schema: {"status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format, separated by ' · '","fit":"one sentence, under 25 words, on what this actually involves","note":"one sentence, under 25 words: status/estimate basis/caveat","noteType":"good, plain, or flag — use flag if not_running or a major caveat","opens_iso":"YYYY-MM-DD when applications open, or null if unknown","deadlines":[{"label":"short specific label, e.g. 'Early Bird Registration'","date_iso":"YYYY-MM-DD"}],"deadline_label":"short text like ROLLING or TBA — only used when the deadlines array is empty","was_estimated":true or false,"requirements":[{"date":"short date text","text":"under 12 words — what's needed, not a repeat of a deadlines entry"}],"apply_url":"the best URL for actually applying","apply_label":"short button label like 'Apply now'","calendar_events":[{"date":"YYYY-MM-DD","text":"under 8 words","type":"deadline, opens, notify, or conference"}],"action_items":["short concrete task, under 10 words", "..."]}. Stay well within a 1000-token response: at most 3 deadlines entries, 3 requirements items, 3 calendar_events, and 5 action_items. Never truncate mid-value or leave the JSON unclosed — shorten or drop optional arrays first, but keep at least the earliest deadline if one exists.`;
  const userContent = `Opportunity: ${opp.name} (${opp.org})\nURL: ${opp.url}\nKnown info: ${opp.summary}\n\nFetch this URL (and the base site if needed), and extract current tracking details per the schema. Look carefully for multiple deadline milestones (early bird vs. regular, etc.) — don't just report the final one.`;
  const raw = await callClaude(system, userContent, true);
  return extractJSON(raw);
}

// ============================================================
// PERSISTENT TRACKER PAGE — separate from the wizard, reconciles
// newly-selected opportunities with whatever's already tracked.
// ============================================================

function findBucketForKind(kind){
  const map = {
    'summer': 'summerPrograms',
    'internship': 'internships',
    'research-competition': 'researchCompetitions',
    'pure-competition': 'pureCompetitions',
    'conference': 'conferences',
    'journal': 'journals'
  };
  return map[kind] || 'summerPrograms';
}
const ALL_BUCKETS = ['summerPrograms', 'internships', 'researchCompetitions', 'pureCompetitions', 'conferences', 'journals'];
const BUCKET_LABELS = {
  summerPrograms: 'Summer Program',
  internships: 'Internship',
  researchCompetitions: 'Research or Project Competition',
  pureCompetitions: 'Academic Competition',
  conferences: 'Conference',
  journals: 'Research Journal'
};

let trackerData = { summerPrograms: [], internships: [], researchCompetitions: [], pureCompetitions: [], conferences: [], journals: [] };
let trackerSavedState = {};
// Ids added to the tracker in the most recent buildTracker() call — drives the "New"
// banner on those cards. Intentionally not persisted: it's cleared the moment the user
// navigates away from the Tracker page (see showPage), so the banner only shows for
// the session in which the opportunity was actually added.
let newlyAddedTrackerIds = new Set();

// Checks whether an opportunity (by id or url) is already present anywhere in the
// tracker, regardless of which bucket it'd currently classify into — used to flag
// "already tracked" results in the Finder and to prevent it from ever being added
// a second time.
function findTrackedItem(opp){
  for(const bucket of ALL_BUCKETS){
    const match = trackerData[bucket].find(i => i.id === opp.id || (opp.url && i.url === opp.url));
    if(match) return { item: match, bucket };
  }
  return null;
}

// ---------- Persistence (per-account, via AppStorage — see top of file) ----------
async function loadTrackerData(){
  try{
    const r = await AppStorage.get('hs-tracker-data');
    if(r && r.value){
      const parsed = JSON.parse(r.value);
      // One-time migration from the old 4-bucket shape (competitions, summerPrograms combined
      // internships+summer) into the new 6-bucket shape. Best-effort — old items land in a
      // reasonable default bucket since we don't have per-item origin metadata to split them precisely.
      if(Array.isArray(parsed.competitions) && !Array.isArray(parsed.researchCompetitions)){
        parsed.researchCompetitions = parsed.competitions;
      }
      trackerData = {
        summerPrograms: Array.isArray(parsed.summerPrograms) ? parsed.summerPrograms : [],
        internships: Array.isArray(parsed.internships) ? parsed.internships : [],
        researchCompetitions: Array.isArray(parsed.researchCompetitions) ? parsed.researchCompetitions : [],
        pureCompetitions: Array.isArray(parsed.pureCompetitions) ? parsed.pureCompetitions : [],
        conferences: Array.isArray(parsed.conferences) ? parsed.conferences : [],
        journals: Array.isArray(parsed.journals) ? parsed.journals : []
      };
      // Migration: older saved items predate the action-items feature — default to empty.
      ALL_BUCKETS.forEach(b => trackerData[b].forEach(item => {
        if(!Array.isArray(item.actionItems)) item.actionItems = [];
      }));
    }
  }catch(e){ /* nothing saved yet, or storage unavailable — start fresh */ }
}
async function saveTrackerData(){
  try{ await AppStorage.set('hs-tracker-data', JSON.stringify(trackerData)); }
  catch(e){ /* storage unavailable — stays in-memory only for this session */ }
}
async function loadTrackerSaved(){
  try{
    const r = await AppStorage.get('hs-tracker-saved');
    if(r && r.value){ trackerSavedState = JSON.parse(r.value); }
  }catch(e){}
}
async function saveTrackerSaved(){
  try{ await AppStorage.set('hs-tracker-saved', JSON.stringify(trackerSavedState)); }catch(e){}
}

// ---------- Page switching (Finder wizard <-> persistent Tracker) ----------
function showPage(name){
  ['home','wizard','tracker','profile'].forEach(p => {
    const el = document.getElementById('page-' + p);
    if(el) {
      if(p === name) {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
      el.style.display = '';
    }
  });

  ['home','wizard','tracker','profile'].forEach(p => {
    const btnId = 'nav' + p.charAt(0).toUpperCase() + p.slice(1) + 'Btn';
    const btn = document.getElementById(btnId);
    if(btn) btn.classList.toggle('active', p === name);
  });

  // The "New" banner on freshly-added tracker cards only lasts until the user leaves
  // the Tracker screen — clear it as soon as we navigate anywhere else.
  if(name !== 'tracker' && newlyAddedTrackerIds.size){ newlyAddedTrackerIds.clear(); }
  if(name === 'tracker'){ renderTrackerPage(); }
  if(name === 'home'){ renderHomePage(); }
  // Always land on step 1 (choose opportunity type) when entering the Finder from
  // outside — otherwise it'd resume whatever stage (results, quiz, etc.) was last left
  // active, which is confusing when you're starting a fresh search.
  if(name === 'wizard'){ renderSuggestEntryCard(); goStage(0); }
  if(name === 'profile'){ renderProfile(); renderProfileFit(); initProfileChat(); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
// ---------- Calendar/List toggle + type filter within the Tracker page ----------
function toggleNavDropdownPanel(panelId){
  const panel = document.getElementById(panelId);
  if(!panel) return;
  const willOpen = !panel.classList.contains('open');
  document.querySelectorAll('.nav-dropdown-panel').forEach(p => p.classList.remove('open'));
  if(willOpen) panel.classList.add('open');
}
document.addEventListener('click', (e) => {
  if(!e.target.closest('.nav-dropdown')){
    document.querySelectorAll('.nav-dropdown-panel').forEach(p => p.classList.remove('open'));
  }
});

let trackerOppView = 'calendar';
function setOppView(view){
  trackerOppView = view;
  updateOppViewUI();
}
// Used by the Home "N tracked" counter — jumps straight to the Tracker's calendar view.
function goToTrackerCalendar(){
  setOppView('calendar');
  showPage('tracker');
}
function updateOppViewUI(){
  document.getElementById('oppViewCalendar').classList.toggle('hidden', trackerOppView !== 'calendar');
  document.getElementById('oppViewList').classList.toggle('hidden', trackerOppView !== 'list');
  document.getElementById('oppViewCalendarBtn').classList.toggle('active', trackerOppView === 'calendar');
  document.getElementById('oppViewListBtn').classList.toggle('active', trackerOppView === 'list');
}

function goToTrackerCard(id){
  if(!id) return;
  setOppView('list');
  requestAnimationFrame(() => {
    const card = document.getElementById('tracker-card-' + id);
    if(!card) return;
    card.scrollIntoView({ behavior:'smooth', block:'center' });
    card.classList.add('jump-highlight');
    setTimeout(() => card.classList.remove('jump-highlight'), 1600);
  });
}

// ---------- Date/badge helpers (multi-milestone deadlines) ----------
function daysUntil(iso){
  const now = new Date();
  const target = new Date(iso + 'T23:59:59');
  return Math.ceil((target - now) / 86400000);
}
function shortDate(iso){
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric'}).toUpperCase();
}
// Gathers every known date on an item (each deadline milestone, plus the
// opens date if known) and returns whichever comes soonest that hasn't
// already passed — "the first deadline the system finds," not necessarily
// the final/regular one. Falls back to the latest known date if everything
// has already passed.
function earliestUpcoming(item){
  const candidates = [];
  if(item.opensISO) candidates.push({ date: item.opensISO, label: 'Opens', kind: 'opens' });
  (item.deadlines || []).forEach(d => {
    if(d.dateISO) candidates.push({ date: d.dateISO, label: d.label, kind: 'deadline' });
  });
  if(!candidates.length) return null;
  const future = candidates.filter(c => daysUntil(c.date) >= 0);
  future.sort((a, b) => a.date.localeCompare(b.date));
  if(future.length) return future[0];
  candidates.sort((a, b) => a.date.localeCompare(b.date));
  return candidates[candidates.length - 1];
}
// System-controlled progress status — derived from the item's known milestones, not
// user-editable. The "first step" is whichever comes earliest (the opens/registration
// date if known, otherwise the earliest deadline) — if that hasn't happened yet, the
// event is entirely in the future. Once the first step has started, the event is
// "happening now" until the LAST known milestone (the latest deadline — typically
// when judging/review for the cycle wraps up) has also passed, at which point the
// cycle is complete. A program flagged not_running is always treated as complete.
const PROGRESS_STATUS_LABEL = { not_started: 'Future Event', in_progress: 'Happening Now', completed: 'Past Event' };
// Separate label set for individual action-item (sub-task) statuses — these are
// user-toggled to-do states, not the opportunity's own event timing, so they keep
// task-style wording instead of the "event" language used for opportunity status.
const ACTION_ITEM_STATUS_LABEL = { not_started: 'Not Started', in_progress: 'In Progress', completed: 'Completed' };
function computeProgressStatus(item){
  if(item.status === 'not_running') return 'completed';
  const dates = [];
  if(item.opensISO) dates.push(item.opensISO);
  (item.deadlines || []).forEach(d => { if(d.dateISO) dates.push(d.dateISO); });
  if(!dates.length) return 'not_started';
  dates.sort();
  const firstStep = dates[0];
  const lastStep = dates[dates.length - 1];
  if(daysUntil(firstStep) > 0) return 'not_started'; // first step (registration/opens) hasn't happened yet
  if(daysUntil(lastStep) < 0) return 'completed'; // last known milestone for the cycle has passed
  return 'in_progress';
}
// Opportunity event-timing state pill — always green/blue/grey for Happening Now /
// Future Event / Past Event, everywhere in the app (see .status-pill.status-opp-* in styles.css).
function statusPillHTML(status){
  return `<span class="status-pill status-opp-${status}">${PROGRESS_STATUS_LABEL[status]}</span>`;
}
// Shared segmented progress bar + legend, used by the Home "Opportunities you are tracking" card
// (kind:'opp' — green/blue/grey) and the "Coming up" to-do list (kind:'task' — red/orange/green).
function progressBarHTML(counts, total, labels = PROGRESS_STATUS_LABEL, order = ['in_progress', 'not_started', 'completed'], kind = 'opp'){
  if(!total){
    return { track: '', legend: '<p class="empty-state">Nothing here yet.</p>' };
  }
  const track = order.map(k => `<div class="progress-seg seg-${kind}-${k}" style="width:${(counts[k] / total * 100)}%"></div>`).join('');
  const legend = order.map(k => `
    <span class="progress-legend-item text-xs font-bold text-slate-600">
      <span class="progress-legend-dot seg-${kind}-${k}"></span> ${labels[k]} (${counts[k]})
    </span>
  `).join('');
  return { track, legend };
}
// Golden-angle hue spacing keeps colors for different opportunities visually far
// apart on the wheel (unlike a plain hash-mod-360, which tends to cluster nearby
// hues for similar-looking seeds) while still being fully deterministic per seed —
// same opportunity always gets the same color, across every month it appears in.
const GOLDEN_ANGLE = 137.508;
function hashColor(seed){
  let hash = 0;
  for(let i = 0; i < seed.length; i++){ hash = seed.charCodeAt(i) + ((hash << 5) - hash); }
  const hue = Math.abs(hash * GOLDEN_ANGLE) % 360;
  return { bg:`hsl(${hue}, 78%, 88%)`, border:`hsl(${hue}, 75%, 42%)`, text:`hsl(${hue}, 80%, 24%)` };
}
// Curated, maximally-spread-apart hues (picked by hand, not evenly stepped, so
// even neighboring palette slots stay visually distinguishable) for calendar
// entry colors. Even with golden-angle spacing, hashColor() picks a hue
// per-opportunity independently, so with enough items on screen at once two
// unrelated opportunities can still land on the same or a near-identical hue.
// assignCalendarColors() instead hands out these hues in first-appearance
// order across everything currently visible in the calendar, guaranteeing no
// two simultaneously-displayed opportunities collide until the palette itself
// runs out (at which point it falls back to hashColor for the overflow).
const CALENDAR_PALETTE_HUES = [210, 20, 150, 280, 45, 340, 170, 265, 5, 195, 320, 95, 240, 60, 300, 130];
const CALENDAR_PALETTE = CALENDAR_PALETTE_HUES.map(hue => (
  { bg:`hsl(${hue}, 78%, 88%)`, border:`hsl(${hue}, 75%, 42%)`, text:`hsl(${hue}, 80%, 24%)` }
));
function assignCalendarColors(venueIds){
  const map = new Map();
  let next = 0;
  venueIds.forEach(id => {
    if(map.has(id)) return;
    map.set(id, next < CALENDAR_PALETTE.length ? CALENDAR_PALETTE[next++] : hashColor(id));
  });
  return map;
}
const MONTH_NAMES = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

// ---------- Card rendering ----------
function toggleTrackerSaved(id){
  trackerSavedState[id] = !trackerSavedState[id];
  saveTrackerSaved();
  renderTrackerPage();
}
// Permanently removes an item from whichever bucket holds it (and clears its saved-for-later
// flag, if any). Uses the same same-button double-click-confirm pattern as clearProfile/
// resetChecklist, since native confirm() is silently blocked in this artifact environment.
// Keyed per item id (not a single shared flag) so arming one card's delete button doesn't
// affect any other card.
let trackerDeleteArmed = {};
function deleteTrackerItem(id, btn){
  if(!trackerDeleteArmed[id]){
    trackerDeleteArmed[id] = true;
    if(!btn.dataset.originalText) btn.dataset.originalText = btn.textContent;
    btn.textContent = 'Click again to delete';
    btn.classList.add('confirm-armed');
    setTimeout(() => {
      if(trackerDeleteArmed[id]){
        trackerDeleteArmed[id] = false;
        btn.textContent = btn.dataset.originalText;
        btn.classList.remove('confirm-armed');
      }
    }, 3000);
    return;
  }
  delete trackerDeleteArmed[id];
  for(const bucket of ALL_BUCKETS){
    const idx = trackerData[bucket].findIndex(i => i.id === id);
    if(idx !== -1){ trackerData[bucket].splice(idx, 1); break; }
  }
  delete trackerSavedState[id];
  saveTrackerData();
  saveTrackerSaved();
  renderTrackerPage();
}
function trackerCardHTML(item, sourceLabel){
  const notRunningBadge = item.status === 'not_running'
    ? `<span class="bg-rose-100 text-rose-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">Not running</span>`
    : '';
  const typeBadge = sourceLabel ? `<span class="bg-violet-200 text-violet-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">${sourceLabel}</span>` : '';
  const estimatedNote = item.wasEstimated && item.status !== 'not_running'
    ? `<div class="bg-yellow-200 border-2 border-slate-900 rounded-xl px-4 py-2.5"><p class="text-xs font-bold text-amber-800">Predicted dates from past cycle.</p></div>`
    : '';
  const deadlineRows = (item.deadlines && item.deadlines.length)
    ? `<div class="space-y-2">
         ${item.deadlines.map(d => `<div class="flex items-center gap-3 text-xs font-bold text-slate-800"><span class="bg-white border-2 border-slate-900 px-2.5 py-1 rounded-lg uppercase tracking-wide shrink-0">${shortDate(d.dateISO)}</span> ${d.label}</div>`).join('')}
       </div>`
    : '';
  const isSaved = !!trackerSavedState[item.id];
  const progress = computeProgressStatus(item);
  const bgClass = progress === 'in_progress' ? 'bg-emerald-50' : 'bg-white';
  // Shown only for the batch of opportunities added in the current session (cleared
  // as soon as the user navigates away from the Tracker screen — see showPage).
  const newBanner = newlyAddedTrackerIds.has(item.id)
    ? `<span class="absolute -left-2 -top-2 bg-lime-300 text-slate-900 font-extrabold text-[10px] uppercase px-3 py-1 rounded-lg border-2 border-slate-900 shadow-sm z-10">New</span>`
    : '';

  return `
    <div class="pop-card ${bgClass} rounded-3xl p-5 sm:p-6 space-y-4 border-4 border-slate-900 relative ${item.status === 'not_running' ? 'opacity-60' : ''}" id="tracker-card-${item.id}">
      ${newBanner}
      <div class="flex justify-between items-start gap-2">
        <div class="flex flex-wrap gap-2">
          ${typeBadge}
          ${notRunningBadge}
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button onclick="event.stopPropagation(); toggleTrackerSaved('${item.id}')" class="w-9 h-9 rounded-full bg-white border-2 border-slate-900 flex items-center justify-center hover:scale-105 transition-transform" title="${isSaved ? 'Restore' : 'Save'}">${isSaved ? '★' : '☆'}</button>
          <button onclick="event.stopPropagation(); deleteTrackerItem('${item.id}', this)" class="w-9 h-9 rounded-full bg-white border-2 border-slate-900 flex items-center justify-center text-slate-500 hover:text-rose-600 transition-colors" title="Delete">✕</button>
        </div>
      </div>

      <div>
        <h3 class="font-heading text-2xl sm:text-3xl font-extrabold text-slate-900 leading-tight"><a href="${item.url}" target="_blank" class="hover:underline">${item.name}</a></h3>
        <p class="text-sm text-slate-500 font-medium mt-1 line-clamp-1">${item.meta || ''}</p>
      </div>

      ${estimatedNote}
      ${deadlineRows}

      <details class="text-xs text-slate-500 cursor-pointer">
        <summary class="font-bold text-indigo-600 hover:underline list-none">▶ Show details</summary>
        <div class="mt-2 bg-slate-50 p-3 rounded-xl border border-slate-200">
          <p class="mb-1">${item.fit}</p>
          ${item.requirements ? item.requirements.map(r => `<div class="flex gap-2 mb-1"><span class="font-bold">${r.date}</span><span>${r.text}</span></div>`).join('') : ''}
          <p class="italic text-[10px] mt-1">${item.note}</p>
        </div>
      </details>

      <div class="flex flex-wrap justify-between items-center gap-3 pt-3 border-t-2 border-slate-100">
        ${statusPillHTML(progress)}
        <a href="${item.applyUrl}" target="_blank" class="pop-btn bg-orange-500 text-slate-900 border-2 border-slate-900 font-extrabold text-xs px-5 py-2.5 rounded-full">${item.applyLabel}</a>
      </div>
    </div>
  `;
}
// Groups opportunities by event timing first — Happening Now, then Future Event, then
// Past Event (which also covers not_running items, since computeProgressStatus treats
// those as completed) — then sorts by soonest deadline within each group.
const TRACKER_STATUS_ORDER = { in_progress: 0, not_started: 1, completed: 2 };
function sortedByTrackerDeadline(list){
  const earliestDateOnly = (item) => {
    const next = earliestUpcoming(item);
    return next ? next.date : '9999-12-31';
  };
  return [...list].sort((a, b) => {
    const statusDiff = TRACKER_STATUS_ORDER[computeProgressStatus(a)] - TRACKER_STATUS_ORDER[computeProgressStatus(b)];
    if(statusDiff !== 0) return statusDiff;
    return earliestDateOnly(a).localeCompare(earliestDateOnly(b));
  });
}

// ---------- Calendar (derived live from each item's deadlines/opens — no separate cache to go stale) ----------
function deriveKeyDatesForItems(items){
  const dates = [];
  items.forEach(item => {
    if(item.status === 'not_running') return;
    const shortLabel = item.name.length > 22 ? item.name.slice(0, 20) + '…' : item.name;
    if(item.opensISO){ dates.push({ date: item.opensISO, label: shortLabel, venueId: item.id, text: 'Opens', type: 'opens' }); }
    (item.deadlines || []).forEach(d => {
      if(d.dateISO) dates.push({ date: d.dateISO, label: shortLabel, venueId: item.id, text: d.label, type: 'deadline' });
    });
  });
  return dates;
}
function monthCardHTML(ym, entries, isCurrent, colorMap){
  const [y, m] = ym.split('-');
  return `
    <div class="month-card${isCurrent ? ' current-month' : ''}">
      <div class="month-head">${MONTH_NAMES[parseInt(m,10)-1]} ${y}</div>
      <div class="month-entries">
        ${entries.map(e => {
          const c = colorMap.get(e.venueId) || hashColor(e.venueId);
          return `
          <div class="month-entry" style="background:${c.bg};border-left-color:${c.border};cursor:pointer;" onclick="goToTrackerCard('${e.venueId}')" title="Jump to ${e.label}">
            <span class="day" style="color:${c.text};">${parseInt(e.date.slice(8,10),10)}</span>
            <span class="entry-text" style="color:${c.text};">
              <strong style="color:${c.text};">${e.label}</strong> — ${e.text}
              <span class="entry-type">${e.type}</span>
            </span>
          </div>
        `;}).join('')}
      </div>
    </div>
  `;
}
// One combined calendar with a swimlane per opportunity type — replaces the old
// three-calendar layout so all deadlines live in a single scannable view.
function renderCalendarSwimlanes(){
  const container = document.getElementById('calendarSwimlanes');
  if(!container) return;
  const now = new Date();
  const currentYM = now.toISOString().slice(0,7);

  const lanes = ALL_BUCKETS.map(bucket => {
    const items = trackerData[bucket].filter(i => !trackerSavedState[i.id]);
    const dates = deriveKeyDatesForItems(items);
    return { bucket, label: BUCKET_LABELS[bucket], dates };
  }).filter(lane => lane.dates.length);

  if(!lanes.length){
    container.innerHTML = '<p class="empty-state">Nothing on the calendar yet — add opportunities via the Finder or the button above.</p>';
    return;
  }

  // Build one color map across every lane so the same opportunity gets the same
  // color everywhere it appears, and no two different opportunities visible at
  // once share a color (see assignCalendarColors above).
  const allVenueIds = [];
  lanes.forEach(lane => lane.dates.forEach(d => allVenueIds.push(d.venueId)));
  const colorMap = assignCalendarColors(allVenueIds);

  container.innerHTML = lanes.map(lane => {
    const byMonth = {};
    lane.dates.forEach(d => { const ym = d.date.slice(0,7); (byMonth[ym] = byMonth[ym] || []).push(d); });
    const months = Object.keys(byMonth).sort();
    const monthsHTML = months.map(ym => monthCardHTML(ym, byMonth[ym].sort((a,b) => a.date.localeCompare(b.date)), ym === currentYM, colorMap)).join('');
    return `
      <div class="calendar-swimlane">
        <div class="swimlane-head text-xs font-bold uppercase tracking-wide text-slate-500 mb-2">${lane.label}</div>
        <div class="flex gap-4 overflow-x-auto pb-2 calendar-strip">${monthsHTML}</div>
      </div>
    `;
  }).join('');
}

// ============================================================
// HOME PAGE — stats and the synthesized profile
// ============================================================

// ---------- Stats ----------
function computeStats(){
  const stats = { total: 0, not_started: 0, in_progress: 0, completed: 0 };
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].forEach(item => {
      if(trackerSavedState[item.id]) return; // saved-for-later isn't "actively tracked"
      stats.total++;
      const p = computeProgressStatus(item);
      if(stats[p] !== undefined) stats[p]++;
    });
  });
  return stats;
}
function renderStats(){
  const s = computeStats();
  document.getElementById('statTotal').textContent = `${s.total} tracked`;
  const bars = progressBarHTML({ not_started: s.not_started, in_progress: s.in_progress, completed: s.completed }, s.total);
  document.getElementById('homeProgressTrack').innerHTML = bars.track;
  document.getElementById('homeProgressLegend').innerHTML = bars.legend;

  const ctaEl = document.getElementById('homeTrackCTA');
  if(ctaEl){
    ctaEl.innerHTML = s.total === 0
      ? `<button class="w-full pop-btn bg-orange-500 text-slate-900 font-extrabold text-sm px-5 py-3.5 rounded-2xl flex items-center justify-center gap-2" onclick="showPage('wizard')">🔍 Find your first opportunity to track →</button>`
      : `<button class="pop-btn bg-white text-slate-900 font-bold text-xs px-4 py-2 rounded-xl" onclick="showPage('wizard')">+ Find more opportunities to track</button>`;
  }
}

// ---------- Home to-do list (imminent deadlines this month + next) ----------
// Counts individual tasks (AI-generated action items) only — NOT the opportunities
// themselves — so this reflects exactly how many tasks are not started, in progress,
// and completed. (Opportunity-level event timing has its own separate status system —
// Future/Happening Now/Past Event — surfaced elsewhere via computeProgressStatus/
// statusPillHTML, and must not be mixed into this task count.)
function allTodoUnitCounts(upcoming){
  const counts = { not_started: 0, in_progress: 0, completed: 0 };
  let total = 0;
  upcoming.forEach(({ item }) => {
    (item.actionItems || []).forEach(ai => { counts[ai.state] = (counts[ai.state] || 0) + 1; total++; });
  });
  return { counts, total };
}
function renderHomeTodo(){
  const listEl = document.getElementById('homeTodoList');
  const trackEl = document.getElementById('todoProgressTrack');
  if(!listEl || !trackEl) return;
  const upcoming = getUpcomingDeadlineItems();
  const { counts, total } = allTodoUnitCounts(upcoming);
  const bars = progressBarHTML(counts, total, ACTION_ITEM_STATUS_LABEL, ['not_started', 'in_progress', 'completed'], 'task');
  trackEl.innerHTML = bars.track;
  const statCountsEl = document.getElementById('todoStatCounts');
  if(statCountsEl){
    const statOrder = ['not_started', 'in_progress', 'completed'];
    statCountsEl.innerHTML = statOrder.map(k => `<span class="status-pill status-task-${k}">${counts[k]} ${ACTION_ITEM_STATUS_LABEL[k]}</span>`).join('');
  }
  // "Due soon" badge in the welcome banner — outstanding (not-yet-completed) tasks
  // among this-month-and-next upcoming items, so it reflects real work still ahead.
  const dueSoonEl = document.getElementById('homeDueSoonCount');
  if(dueSoonEl) dueSoonEl.textContent = counts.not_started + counts.in_progress;

  if(!upcoming.length){
    listEl.innerHTML = `<p class="empty-state">Nothing due this month or next — you're all caught up.</p>`;
    return;
  }
  // Summarized one row per opportunity — task-level detail lives in the "View all
  // tasks" modal (grouped by opportunity, each task individually toggleable).
  listEl.innerHTML = upcoming.map(({ item, nextDate, nextLabel }) => {
    const status = computeProgressStatus(item);
    const taskCount = (item.actionItems || []).length;
    return `
      <div class="flex items-center justify-between gap-3 py-2 border-b border-slate-100 last:border-0">
        <div class="min-w-0">
          <p class="font-bold text-sm text-slate-900 truncate">${item.name}</p>
          <p class="text-xs text-slate-500">${shortDate(nextDate)} · ${nextLabel}${taskCount ? ` · ${taskCount} task${taskCount > 1 ? 's' : ''}` : ''}</p>
        </div>
        ${statusPillHTML(status)}
      </div>
    `;
  }).join('');
  listEl.insertAdjacentHTML('beforeend', `<button class="w-full text-center text-xs font-bold text-indigo-600 hover:underline pt-2" onclick="event.stopPropagation(); openTodoModal();">View all tasks →</button>`);
}

// ---------- Home to-do expand modal ----------
const NEXT_ACTION_STATE = { not_started: 'in_progress', in_progress: 'completed', completed: 'not_started' };
function cycleActionItemState(itemId, actionId){
  for(const bucket of ALL_BUCKETS){
    const item = trackerData[bucket].find(i => i.id === itemId);
    if(item){
      const ai = (item.actionItems || []).find(a => a.id === actionId);
      if(ai){
        ai.state = NEXT_ACTION_STATE[ai.state] || 'not_started';
        saveTrackerData();
        renderHomeTodo();
        renderTodoModalContent();
      }
      return;
    }
  }
}
function renderTodoModalContent(){
  const wrap = document.getElementById('todoModalBody');
  if(!wrap) return;
  const upcoming = getUpcomingDeadlineItems();
  if(!upcoming.length){
    wrap.innerHTML = `<p class="empty-state">Nothing due this month or next — you're all caught up.</p>`;
    return;
  }
  wrap.innerHTML = upcoming.map(({ item, nextDate, nextLabel }) => {
    const status = computeProgressStatus(item);
    const actionRows = (item.actionItems || []).map(ai => `
      <div class="flex items-center justify-between gap-3 py-1.5">
        <span class="text-xs font-medium text-slate-700 ${ai.state === 'completed' ? 'line-through text-slate-400' : ''}">${ai.text}</span>
        <button class="status-pill status-task-${ai.state} cursor-pointer" onclick="cycleActionItemState('${item.id}','${ai.id}')" title="Click to change status">${ACTION_ITEM_STATUS_LABEL[ai.state]}</button>
      </div>
    `).join('');
    return `
      <div class="bg-slate-50 border-2 border-slate-200 rounded-2xl p-4">
        <div class="flex items-start justify-between gap-3 mb-1">
          <div class="min-w-0">
            <h4 class="font-bold text-sm text-slate-900 truncate"><a href="${item.url}" target="_blank" class="hover:underline">${item.name}</a></h4>
            <p class="text-xs text-slate-500 line-clamp-1">${item.meta || ''}</p>
          </div>
          ${statusPillHTML(status)}
        </div>
        <p class="text-xs font-bold text-indigo-600 mb-2">${shortDate(nextDate)} · ${nextLabel}${item.wasEstimated ? ' (est.)' : ''}</p>
        ${actionRows ? `<div class="border-t border-slate-200 pt-2 mt-2 space-y-0.5">${actionRows}</div>` : `<p class="text-xs text-slate-400 italic">No sub-tasks generated for this one.</p>`}
      </div>
    `;
  }).join('');
}
function openTodoModal(){
  renderTodoModalContent();
  document.getElementById('todoModal').classList.remove('hidden');
  lockBodyScroll();
}
function closeTodoModal(){
  document.getElementById('todoModal').classList.add('hidden');
  unlockBodyScroll();
}

// ---------- Dashboard profile teaser: urgent nudge toward the Profile tab ----------
// Deliberately lightweight — the full summary + editor + chat live on the Profile tab
// (renderProfileFit below). This card's only job is to make "go build your profile" feel
// urgent and immediate: pulsing/bright when there's nothing (or stale info) to work with,
// calmer once there's a fresh profile, but always a one-click jump via goToProfile().
function renderHomeProfileTeaser(){
  const wrap = document.getElementById('homeProfileTeaser');
  if(!wrap) return;
  const hasProfile = !!studentProfile.synthesized;
  const days = studentProfile.updatedAt ? daysSince(studentProfile.updatedAt) : null;
  const isStale = hasProfile && days !== null && days >= PROFILE_STALE_DAYS;

  if(!hasProfile){
    wrap.innerHTML = `
      <div class="pop-card urgent-pulse bg-gradient-to-br from-orange-400 to-rose-500 text-white p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4">
        <div>
          <p class="font-heading font-extrabold text-lg">⚡ Your profile is empty!</p>
          <p class="text-sm font-medium opacity-90 mt-1 max-w-md">Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.</p>
        </div>
        <button class="pop-btn bg-white text-slate-900 font-bold px-4 py-2.5 rounded-xl text-sm shrink-0" onclick="goToProfile()">Build my profile →</button>
      </div>
    `;
    return;
  }

  if(isStale){
    wrap.innerHTML = `
      <div class="pop-card urgent-pulse bg-amber-100 border-2 border-amber-500 p-6 rounded-3xl flex flex-wrap items-center justify-between gap-4">
        <div>
          <p class="font-heading font-extrabold text-lg text-amber-900">⏰ Your profile is ${days} days old</p>
          <p class="text-sm font-medium text-amber-800 mt-1 max-w-md">Stale profiles mean stale matches — a quick refresh keeps your suggestions sharp.</p>
        </div>
        <button class="pop-btn bg-orange-500 text-slate-900 font-bold px-4 py-2.5 rounded-xl text-sm shrink-0" onclick="goToProfile()">Update my profile →</button>
      </div>
    `;
    return;
  }

  wrap.innerHTML = `
    <div class="pop-card bg-white p-6 rounded-3xl space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="font-heading font-bold text-xl">Your Story So Far</h2>
        <button class="pop-btn bg-orange-500 text-slate-900 font-bold px-4 py-2.5 rounded-xl text-sm shrink-0" onclick="goToProfile()">View &amp; deepen it →</button>
      </div>
      <p class="text-sm text-slate-500 font-medium line-clamp-3">${escapeHtmlTracker(studentProfile.synthesized)}</p>
    </div>
  `;
}

// ---------- Profile tab: single synthesized profile (read-only summary) ----------
// Static by design — the summary itself is never directly editable. The only action
// available here is clearing it completely; every add/update/correction happens through
// the chat below (see the Profile Builder Chat block), which is the sole source of truth
// for what gets merged into this summary.
function renderProfileFit(){
  const wrap = document.getElementById('profileFitSection');
  if(!wrap) return;

  if(!studentProfile.synthesized){
    wrap.innerHTML = `
      <p class="empty-state">Nothing here yet — chat with the bot below to build your profile.</p>
      <button class="w-full pop-btn bg-orange-500 text-slate-900 font-extrabold text-sm px-5 py-3.5 rounded-2xl flex items-center justify-center gap-2" onclick="focusProfileChat()">↓ Start chatting</button>
    `;
    return;
  }

  wrap.innerHTML = `
    ${profileSummaryBodyHTML(studentProfile.synthesized)}
    <div class="flex gap-3 pt-4 border-t-2 border-slate-100">
      <button class="text-xs font-bold text-rose-600 hover:underline" onclick="clearProfile(this)">🗑 Clear profile</button>
    </div>
  `;
}

// Splits a synthesized profile into readable paragraphs, pulling out any
// "Passion Project: " / "Research Project: " paragraphs into their own separate,
// individually-numbered sections rather than letting them blend in with the general
// interests text (or with each other). Shared by the Dashboard profile card (renderProfileFit)
// and the Finder's "Here's what we know about you" card, so both render identically.
function profileSummaryBodyHTML(text){
  const allParagraphs = (text || '').split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  const passionProjects = [];
  const researchProjects = [];
  const generalParagraphs = [];
  allParagraphs.forEach(p => {
    if(/^passion projects?:/i.test(p)) passionProjects.push(p.replace(/^passion projects?:\s*/i, ''));
    else if(/^research projects?:/i.test(p)) researchProjects.push(p.replace(/^research projects?:\s*/i, ''));
    else generalParagraphs.push(p);
  });

  const generalHTML = generalParagraphs.map(p => `<p class="text-sm text-slate-700 leading-relaxed font-medium mb-3 last:mb-0">${escapeHtmlTracker(p)}</p>`).join('');

  const numberedListHTML = items => `
    <ol class="space-y-3">
      ${items.map((p, i) => `
        <li class="flex gap-2">
          <span class="font-heading font-bold text-indigo-700 text-sm shrink-0">${i + 1}.</span>
          <p class="text-sm text-slate-700 leading-relaxed font-medium">${escapeHtmlTracker(p)}</p>
        </li>
      `).join('')}
    </ol>
  `;

  const passionHTML = passionProjects.length ? `
    <div class="mt-4 pt-4 border-t-2 border-indigo-200">
      <h4 class="font-heading font-bold text-xs uppercase tracking-wide text-indigo-700 mb-2">🚀 Passion Projects</h4>
      ${numberedListHTML(passionProjects)}
    </div>
  ` : '';
  const researchHTML = researchProjects.length ? `
    <div class="mt-4 pt-4 border-t-2 border-indigo-200">
      <h4 class="font-heading font-bold text-xs uppercase tracking-wide text-indigo-700 mb-2">🔬 Research Projects</h4>
      ${numberedListHTML(researchProjects)}
    </div>
  ` : '';

  return generalHTML + passionHTML + researchHTML;
}

// ---------- Deadlines due this month & next ----------
// Deliberately generic, opportunity-agnostic fallback checklist — used only when an
// item has no AI-generated action items yet (e.g. the extraction call failed or an
// item was added before this field existed). Kept purely administrative: the app only
// knows what's publicly listed for the opportunity, never the specifics of the
// student's own project, so it must not guess at anything more specific than this.
const GENERIC_ACTION_ITEMS = [
  'Confirm the exact deadline and requirements on the official site',
  'Gather required materials (transcripts, recommendation letters, etc.)',
  'Complete and submit the application'
];
function ensureActionItems(item){
  if(!item.actionItems || !item.actionItems.length){
    item.actionItems = GENERIC_ACTION_ITEMS.map((text, i) => ({ id: `${item.id}-gt${i}`, text, state: 'not_started' }));
    return true; // backfilled — caller should persist
  }
  return false;
}
function getUpcomingDeadlineItems(){
  const now = new Date();
  const thisMonthKey = now.getFullYear() * 12 + now.getMonth();
  const nextMonthKey = thisMonthKey + 1;
  const results = [];
  let backfilled = false;
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].forEach(item => {
      if(item.status === 'not_running') return;
      if(trackerSavedState[item.id]) return;
      const next = earliestUpcoming(item);
      if(!next) return;
      const d = new Date(next.date + 'T00:00:00');
      const key = d.getFullYear() * 12 + d.getMonth();
      if(key === thisMonthKey || key === nextMonthKey){
        if(ensureActionItems(item)) backfilled = true;
        results.push({ item, bucket, nextDate: next.date, nextLabel: next.label, nextKind: next.kind });
      }
    });
  });
  if(backfilled) saveTrackerData();
  // Grouped by event timing first — Happening Now, then Future Event, then Past
  // Event — then by soonest deadline within each group (mirrors sortedByTrackerDeadline).
  results.sort((a, b) => {
    const statusDiff = TRACKER_STATUS_ORDER[computeProgressStatus(a.item)] - TRACKER_STATUS_ORDER[computeProgressStatus(b.item)];
    if(statusDiff !== 0) return statusDiff;
    return a.nextDate.localeCompare(b.nextDate);
  });
  return results;
}
// ---------- Full Home page render ----------
function renderHomePage(){
  if(!document.getElementById('statTotal')) return; // page not in DOM yet
  renderStats();
  renderHomeTodo();
  renderHomeProfileTeaser();
}

// Dismisses a status banner (either via the ✕ button, or automatically — banners are
// scoped to the action that produced them and must not persist across page visits).
function dismissBanner(id){
  const el = document.getElementById(id);
  if(el) el.classList.remove('show');
}
function bannerContentHTML(html, id){
  return `<span class="banner-dismiss" onclick="dismissBanner('${id}')" title="Dismiss">✕</span>${html}`;
}
function renderTrackerPage(){
  // Every fresh page-entry starts with no status banner showing — banners are set (and
  // shown) only as the direct result of an action taken during THIS visit (buildTracker,
  // refreshTracker), never carried over from a previous visit.
  dismissBanner('trackerChangeBanner');
  dismissBanner('trackerErrorBanner');

  // Flat list of everything actively tracked (not saved-for-later), across all
  // opportunity types at once — sorted by nearest deadline, each card tagged with its type.
  const visibleItems = [];
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].filter(i => !trackerSavedState[i.id]).forEach(i => visibleItems.push({ item: i, bucket }));
  });
  const sortedItems = sortedByTrackerDeadline(visibleItems.map(x => x.item));
  const bucketByItemId = {};
  visibleItems.forEach(x => { bucketByItemId[x.item.id] = x.bucket; });
  document.getElementById('allOppCards').innerHTML = sortedItems.length
    ? sortedItems.map(i => trackerCardHTML(i, BUCKET_LABELS[bucketByItemId[i.id]])).join('')
    : '<p class="empty-state">Nothing tracked here yet — add opportunities via the Finder or the button above.</p>';

  const savedEntries = [];
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].filter(i => trackerSavedState[i.id]).forEach(i => savedEntries.push({ item: i, label: BUCKET_LABELS[bucket] }));
  });
  const savedSortedItems = sortedByTrackerDeadline(savedEntries.map(s => s.item));
  document.getElementById('savedCards').innerHTML = savedSortedItems.length
    ? savedSortedItems.map(i => trackerCardHTML(i, savedEntries.find(s => s.item.id === i.id).label)).join('')
    : '<p class="empty-state">Nothing saved yet — click "☆ Save for later" on any card to move it here.</p>';
  document.getElementById('savedDrawerCount').textContent = String(savedSortedItems.length).padStart(2, '0');

  renderCalendarSwimlanes();
  renderHomePage();
}

// ---------- Build/reconcile from the wizard's selected results ----------
async function buildTracker(){
  const btn = document.getElementById('buildTrackerBtn');
  const label = document.getElementById('buildTrackerLabel');
  btn.disabled = true;
  btn.classList.add('loading');
  newlyAddedTrackerIds = new Set(); // only this batch should carry the "New" banner

  // Group selected opportunities by their bucket — the profile-based Suggest flow can
  // return results spanning several opportunity kinds at once (each result carries its
  // own r.kind), unlike the single-kind Type flow, which falls back to selectedKind.
  // Already-tracked opportunities are excluded up front (checked across ALL buckets,
  // not just the target one) so a single opportunity can never end up duplicated in
  // the tracker, however it was selected.
  const selectedResults = currentResults.filter(r => selectedIds.has(r.opp.id) && !findTrackedItem(r.opp));
  const byBucket = {};
  selectedResults.forEach(r => {
    const bucket = findBucketForKind(r.kind || selectedKind);
    (byBucket[bucket] = byBucket[bucket] || []).push(r.opp);
  });
  const buckets = Object.keys(byBucket);

  const fetchPlan = [];
  let totalToFetch = 0, totalAlready = 0;
  buckets.forEach(bucket => {
    const opps = byBucket[bucket];
    const toFetch = opps.filter(o => !findTrackedItem(o));
    totalToFetch += toFetch.length;
    totalAlready += opps.length - toFetch.length;
    fetchPlan.push({ bucket, toFetch });
  });

  let done = 0;
  for(const { bucket, toFetch } of fetchPlan){
    for(const opp of toFetch){
      label.textContent = `Fetching details (${++done}/${totalToFetch})…`;
      try{
        let info;
        try{
          info = await extractTrackerInfo(opp);
        }catch(firstErr){
          console.warn(`Retrying ${opp.name} after error:`, firstErr.message);
          info = await extractTrackerInfo(opp);
        }
        trackerData[bucket].push({
          id: opp.id,
          name: opp.name,
          url: opp.url,
          type: opp.type,
          bucket: bucket,
          progressStatus: 'not_started',
          status: ['running','not_running','unknown'].includes(info.status) ? info.status : 'unknown',
          meta: info.meta || [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
          fit: info.fit || opp.summary,
          note: info.note || 'Details from the opportunities database — confirm on the official site.',
          noteType: info.status === 'not_running' ? 'flag' : (info.noteType || 'plain'),
          deadlines: Array.isArray(info.deadlines)
            ? info.deadlines.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Deadline', dateISO: d.date_iso })).sort((a, b) => a.dateISO.localeCompare(b.dateISO))
            : [],
          deadlineLabel: info.deadline_label || 'CHECK SITE',
          wasEstimated: !!info.was_estimated,
          opensISO: info.opens_iso || null,
          requirements: Array.isArray(info.requirements) ? info.requirements.slice(0, 5) : null,
          applyUrl: info.apply_url || opp.url,
          applyLabel: info.apply_label || 'Apply / learn more',
          actionItems: Array.isArray(info.action_items)
            ? info.action_items.slice(0, 5).map((text, i) => ({ id: `${opp.id}-t${i}`, text, state: 'not_started' }))
            : []
        });
        newlyAddedTrackerIds.add(opp.id);
      }catch(err){
        console.error(`Failed to fetch details for ${opp.name}:`, err);
        trackerData[bucket].push({
          id: opp.id, name: opp.name, url: opp.url, type: opp.type,
          bucket: bucket, progressStatus: 'not_started',
          status: 'unknown',
          meta: [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
          fit: opp.summary,
          note: 'Live details couldn\'t be fetched — showing database info only. Check the official site directly.',
          noteType: 'flag',
          deadlines: [], deadlineLabel: 'CHECK SITE', wasEstimated: false, opensISO: null,
          requirements: null, applyUrl: opp.url, applyLabel: 'Visit site', actionItems: []
        });
        newlyAddedTrackerIds.add(opp.id);
      }
    }
  }

  await saveTrackerData();

  btn.disabled = false;
  btn.classList.remove('loading');
  label.textContent = 'Add to my tracker →';

  showPage('tracker');
  setOppView('list');
}

// ---------- Refresh: re-check every tracked item's live status/deadlines ----------
async function refreshTracker(){
  const btn = document.getElementById('trackerRefreshBtn');
  const label = document.getElementById('trackerRefreshBtnLabel');
  const status = document.getElementById('trackerRefreshStatus');
  const changeBanner = document.getElementById('trackerChangeBanner');
  const errorBanner = document.getElementById('trackerErrorBanner');

  btn.disabled = true;
  btn.classList.add('loading');
  changeBanner.classList.remove('show');
  errorBanner.classList.remove('show');

  const allItems = [];
  ALL_BUCKETS.forEach(b => trackerData[b].forEach(item => allItems.push(item)));

  if(!allItems.length){
    btn.disabled = false; btn.classList.remove('loading'); label.textContent = '↻ Check for updates';
    status.textContent = 'Nothing tracked yet — add opportunities first.';
    return;
  }

  const changes = [];
  let failures = 0;

  for(let i = 0; i < allItems.length; i++){
    const item = allItems[i];
    label.textContent = `Checking ${item.name} (${i + 1}/${allItems.length})…`;
    try{
      const info = await extractTrackerInfo({ name: item.name, org: '', url: item.url, summary: item.fit });
      const oldStatus = item.status;
      const oldDeadlinesKey = JSON.stringify(item.deadlines);
      item.status = ['running','not_running','unknown'].includes(info.status) ? info.status : item.status;
      item.meta = info.meta || item.meta;
      item.fit = info.fit || item.fit;
      item.note = info.note || item.note;
      item.noteType = item.status === 'not_running' ? 'flag' : (info.noteType || item.noteType);
      if(Array.isArray(info.deadlines)){
        item.deadlines = info.deadlines.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Deadline', dateISO: d.date_iso })).sort((a, b) => a.dateISO.localeCompare(b.dateISO));
      }
      item.wasEstimated = !!info.was_estimated;
      item.opensISO = info.opens_iso || item.opensISO;
      if(Array.isArray(info.requirements)) item.requirements = info.requirements.slice(0, 5);
      item.applyUrl = info.apply_url || item.applyUrl;
      item.applyLabel = info.apply_label || item.applyLabel;
      if(oldStatus !== item.status || JSON.stringify(item.deadlines) !== oldDeadlinesKey){
        changes.push(`<strong>${item.name}</strong>: updated`);
      }
    }catch(err){
      failures++;
      console.error(`Refresh failed for ${item.name}:`, err);
    }
  }

  await saveTrackerData();
  renderTrackerPage();

  btn.disabled = false;
  btn.classList.remove('loading');
  label.textContent = '↻ Check for updates';
  const stamp = new Date().toLocaleString('en-US', {month:'short', day:'numeric', year:'numeric', hour:'numeric', minute:'2-digit'});
  status.textContent = `Last checked: ${stamp}` + (changes.length === 0 && failures === 0 ? ' — no changes found.' : '');

  if(changes.length){
    changeBanner.innerHTML = bannerContentHTML(`<strong>${changes.length} item${changes.length > 1 ? 's' : ''} updated:</strong><ul>${changes.map(c => `<li>${c}</li>`).join('')}</ul>`, 'trackerChangeBanner');
    changeBanner.classList.add('show');
  }
  if(failures){
    errorBanner.innerHTML = bannerContentHTML(failures === allItems.length
      ? `Couldn't reach the Claude API from this page. Live refresh only works when this file is opened through a Claude-connected environment.`
      : `${failures} item${failures > 1 ? 's' : ''} couldn't be checked (site may be blocking automated access). Left unchanged.`, 'trackerErrorBanner');
    errorBanner.classList.add('show');
  }
}

// ---------- Intake: add a new opportunity directly on the Tracker page ----------
function slugifyTracker(text, bucket){
  let base = (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '').slice(0, 50);
  if(!base) base = 'opportunity';
  let id = base, n = 2;
  const used = new Set(trackerData[bucket].map(i => i.id));
  while(used.has(id)){ id = `${base}-${n}`; n++; }
  return id;
}
async function trackerIntakeExtractAndClassify(url, notes){
  const today = todayLabel();
  const root = baseDomain(url);
  const system = `You classify and extract structured tracking data for a student extracurricular opportunity from a URL, for a high-school tracker. Today's date is ${today}.

First determine 'section': 'conferences' for academic conferences/workshops that review and present papers, 'journals' for academic/student journals with manuscript submission, 'researchCompetitions' for science fairs, app challenges, and project/research-based contests where a project or paper is submitted and judged, 'pureCompetitions' for skills/knowledge tests with no project submitted (olympiads, quiz competitions, exams), 'internships' for hands-on mentored work positions with a lab, company, or organization, 'summerPrograms' for camps, enrichment programs, or coursework.

Search thoroughly with web_search: start with the given URL; if stale or missing, also check the base site (${root}). Look for language indicating the program is discontinued/not running this cycle — set status to "not_running" if so. Find EVERY distinct deadline milestone (e.g. early-bird vs. regular), each with a short label, in chronological order. If every deadline found has passed and the program is recurring, estimate the next cycle's date and set was_estimated true. Never invent a date with no basis.

Pay particular, deliberate attention to the registration/application OPENS date, not just the deadline — actively search for it (check any "key dates" or "timeline" section), and if not found but the program is recurring, estimate it from the prior cycle's opens date the same way you'd estimate a deadline. Only leave opens_iso null if there's genuinely no basis to find or estimate one.

Also think through 3-5 short, concrete action items a student would need to do to meet the nearest deadline (e.g. request a recommendation letter, draft an essay, gather transcripts) — infer these from requirements and what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or its substance, since you don't know the specifics of their work and must not assume or invent any. Skip if status is not_running.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","opens_iso":"YYYY-MM-DD or null","deadlines":[{"label":"short label","date_iso":"YYYY-MM-DD"}],"deadline_label":"short text like ROLLING, only if deadlines is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null","action_items":["short concrete task, under 10 words", "..."]}. Stay well within 1000 tokens: at most 3 deadlines, 3 requirements, and 5 action_items.`;
  const userContent = `URL: ${url}\n${notes ? `Extra context: ${notes}\n` : ''}\nFetch this URL, classify it, and extract tracking details per the schema.`;
  const raw = await callClaude(system, userContent, true);
  return extractJSON(raw);
}
async function trackerAnalyzeAndAdd(){
  const urlInput = document.getElementById('trackerIntakeUrl');
  const notesInput = document.getElementById('trackerIntakeNotes');
  const btn = document.getElementById('trackerIntakeSubmit');
  const label = document.getElementById('trackerIntakeSubmitLabel');
  const status = document.getElementById('trackerIntakeStatus');
  const errorBox = document.getElementById('trackerIntakeError');

  const url = urlInput.value.trim();
  const notes = notesInput.value.trim();
  errorBox.classList.remove('show');
  if(!url){ errorBox.textContent = 'Paste a URL first.'; errorBox.classList.add('show'); return; }
  try{ new URL(url); }catch(e){ errorBox.textContent = 'That doesn\'t look like a valid URL — include https://'; errorBox.classList.add('show'); return; }

  btn.disabled = true;
  btn.classList.add('loading');
  label.textContent = 'Fetching and analyzing…';
  status.textContent = '';

  try{
    const extracted = await trackerIntakeExtractAndClassify(url, notes);
    const bucket = ALL_BUCKETS.includes(extracted.section) ? extracted.section : 'researchCompetitions';
    const id = slugifyTracker(extracted.name || url, bucket);
    const item = {
      id,
      name: extracted.name || url,
      url,
      type: extracted.category || '',
      bucket: bucket,
      progressStatus: 'not_started',
      status: ['running','not_running','unknown'].includes(extracted.status) ? extracted.status : 'unknown',
      meta: extracted.meta || '',
      fit: extracted.fit || '',
      note: extracted.note || 'Added manually via URL.',
      noteType: extracted.status === 'not_running' ? 'flag' : (extracted.noteType || 'plain'),
      deadlines: Array.isArray(extracted.deadlines)
        ? extracted.deadlines.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Deadline', dateISO: d.date_iso })).sort((a, b) => a.dateISO.localeCompare(b.dateISO))
        : [],
      deadlineLabel: extracted.deadline_label || 'CHECK SITE',
      wasEstimated: !!extracted.was_estimated,
      opensISO: extracted.opens_iso || null,
      requirements: Array.isArray(extracted.requirements) ? extracted.requirements.slice(0, 5) : null,
      applyUrl: extracted.apply_url || url,
      applyLabel: extracted.apply_label || 'Apply / learn more',
      actionItems: Array.isArray(extracted.action_items)
        ? extracted.action_items.slice(0, 5).map((text, i) => ({ id: `${id}-t${i}`, text, state: 'not_started' }))
        : []
    };
    trackerData[bucket].push(item);
    await saveTrackerData();
    renderTrackerPage();

    status.textContent = `Added "${item.name}" ✓`;
    urlInput.value = '';
    notesInput.value = '';
    goToTrackerCard(id);
  }catch(err){
    console.error('Tracker intake failed:', err);
    errorBox.textContent = `Couldn't extract details — this only works with live API access. Error: ${err.message}`;
    errorBox.classList.add('show');
  }finally{
    btn.disabled = false;
    btn.classList.remove('loading');
    label.textContent = 'Add';
  }
}

// ---------- To Do (persistent, scoped to the Tracker page) ----------
function escapeHtmlTracker(str){
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ---------- Auth gate ----------
// #appShell stays hidden (and #page-login shown) until a returning session is found or
// the student signs in / registers. Profile/tracker data is loaded fresh per-account
// only once signed in — see showApp() -> loadAccountData().
loadUser().then(() => {
  if(currentUser){ showApp(); } else { showLoginGate(); }
});