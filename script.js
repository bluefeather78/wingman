// Opportunities database is loaded asynchronously from opportunities.json
// The rest of the app initialises inside init() once the fetch resolves.
let OPPORTUNITIES = [];

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
    name: 'Pure Competition',
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
  document.getElementById('stage1InfoIcon').title = cfg.sub;
  document.getElementById('stage1Label').textContent = cfg.label;
  const box = document.getElementById('pInterests');
  box.placeholder = cfg.placeholder;
  box.value = '';
  charCountEl.textContent = '0 characters — aim for at least 200';
  document.getElementById('prefsRow').style.display = cfg.source === 'web' ? 'none' : 'grid';
  document.getElementById('formError').classList.remove('show');
  unlocked[1] = true;
  goStage(1);
}

// ---------- Stage navigation ----------

function goStage(n){
  document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
  document.getElementById('stage-' + n).classList.add('active');
  document.querySelectorAll('.step').forEach(s => s.classList.remove('active'));
  const stepIndex = (n === 'quiz' || n === 'suggest') ? 0 : n; // both are sub-flows of "Type of opportunity"
  for(let i = 0; i <= 2; i++){
    const nav = document.getElementById('stepnav-' + i);
    nav.classList.remove('active', 'done', 'clickable');
    if(i < stepIndex){ nav.classList.add('done'); if(unlocked[i]) nav.classList.add('clickable'); }
    if(i === stepIndex){ nav.classList.add('active'); }
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
const unlocked = { 0: true, 1: false, 2: false };
document.querySelectorAll('.step').forEach(el => {
  el.addEventListener('click', () => {
    const n = parseInt(el.dataset.stage, 10);
    if(unlocked[n]) goStage(n);
  });
});

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
    <p class="quiz-question">${branch.question}</p>
    <div class="quiz-options">
      ${branch.options.map(o => `
        <button class="quiz-option" onclick="selectKind('${o.kind}')">
          <strong>${o.title}</strong>
          <span>${o.desc}</span>
        </button>
      `).join('')}
    </div>
    <button class="back-link" style="margin-top:16px;" onclick="resetQuizToStep1()">← Different answer</button>
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
    system: system,
    messages: [ { role: "user", content: userContent } ]
  };
  if(useWebSearch){
    body.tools = [ { type: "web_search_20250305", name: "web_search" } ];
  }
  const res = await fetch("https://api.anthropic.com/v1/messages", {
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
  const system = "You are helping a student find the best-fit extracurricular opportunities (programs, internships, competitions, research positions) for their specific passion project, from a candidate list. Read their project description carefully and select the opportunities that would genuinely help them grow this specific project, build relevant skills, get recognition for it, or connect with the right community — not just anything thematically adjacent. Rank the best 10-12 matches only. For each, write a short specific reason tied to THEIR project (under 15 words), not a generic description of the opportunity. Assign a tier: 'strong' (excellent, specific fit), 'good' (solid fit), or 'look' (worth considering, broader fit). Respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{\"id\":\"...\",\"reason\":\"...\",\"tier\":\"strong|good|look\"}]. Stay well within a 1000-token response — 10-12 items is a hard cap.";
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
  const system = `You help a student researcher find real, current ${cfg.venueKind} that fit their specific research. Use web_search to find and verify actual venues — don't rely only on memorized knowledge, since deadlines and calls-for-papers change. Prefer venues realistically accessible to a high-school or early-career researcher (student research workshops, high-school-friendly journals, open/inclusive workshops), but you can include 1-2 more ambitious or competitive options too. For each of the best 6-8 matches, respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{\"name\":\"official venue name, include year if known\",\"url\":\"the venue's official URL\",\"org\":\"organizing body, short\",\"summary\":\"under 18 words on scope/format\",\"reason\":\"under 15 words on why it fits THIS research specifically\",\"tier\":\"strong|good|look\"}]. Stay well within a 1000-token response — 6-8 items is a hard cap, keep every field short.`;
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
      season: ''
    };
    return { opp, reason: item.reason || '', tier: ['strong','good','look'].includes(item.tier) ? item.tier : 'good' };
  });
}

// ============================================================
// Persistent student profile — a single synthesized narrative, not a list
// of individual entries. New information (from the Finder, clarifying
// questions, or a direct edit) is merged into it via an API call that adds,
// updates, or drops details as appropriate, so there is only ever one
// current version, carried across sessions via window.storage.
// ============================================================
const ACTIVE_KINDS = Object.keys(KIND_CONFIG).filter(k => !KIND_CONFIG[k].comingSoon);

let studentProfile = { synthesized: '', updatedAt: null };
let editingProfile = false; // true while the Home page "add to profile" textarea is open

function newEntryId(){
  return 'entry-' + Date.now() + '-' + Math.random().toString(36).slice(2, 7);
}
async function loadProfile(){
  try{
    if(window.storage){
      const result = await window.storage.get('student-profile');
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
      }
    }
  }catch(e){ /* nothing saved yet, or storage unavailable — start fresh */ }
}
async function saveProfile(){
  try{
    if(window.storage){
      await window.storage.set('student-profile', JSON.stringify(studentProfile));
    }
  }catch(e){ /* storage unavailable in this environment — profile stays in-memory only for this session */ }
}

function toggleProfile(){
  document.getElementById('profilePanel').classList.toggle('translate-x-full');
}
document.addEventListener('click', (e) => {
  const panel = document.getElementById('profilePanel');
  const toggle = document.getElementById('profileToggle');
  if(panel && toggle && !panel.contains(e.target) && !toggle.contains(e.target)){
    panel.classList.add('translate-x-full');
  }
});

// Compact widget popover (top-right icon) — read-only preview, editing lives on the Home page.
function renderProfile(){
  const el = document.getElementById('profileSynthText');
  if(el){ el.textContent = studentProfile.synthesized || 'Nothing yet.'; }
  const updatedEl = document.getElementById('profileUpdated');
  if(updatedEl){
    updatedEl.textContent = studentProfile.updatedAt
      ? `Last updated ${new Date(studentProfile.updatedAt).toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'})}`
      : 'Not started yet';
  }
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
  studentProfile = { synthesized: '', updatedAt: null };
  editingProfile = false;
  await saveProfile();
  renderProfile();
  renderProfileFit();
  renderSuggestEntryCard();
}

// Merges a block of new text into the single synthesized profile via the API — adding,
// updating, or dropping details as the new information warrants — so only one current
// version ever exists. Falls back to a plain append if the API is unavailable, so
// nothing the student wrote is lost even without live access.
async function synthesizeProfile(existing, newText){
  const system = `You maintain a single, coherent running profile of a high school student's academic and extracurricular interests, built up over multiple sessions. You'll be given the student's CURRENT profile (may be empty) and NEW information they just added. Merge the new information in: add genuinely new details, and update or remove anything the new information supersedes or contradicts. Do not drop specific, still-relevant details from the current profile just because they weren't repeated in the new information. Write it as a concise set of factual statements in third person (not addressed to the student, not a bulleted list). Respond with ONLY the updated profile text — no markdown, no preamble, no quotes around it.`;
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
}

// ---------- Home page: single synthesized profile card (edit / delete) ----------
function startProfileEdit(){
  editingProfile = true;
  renderProfileFit();
  const box = document.getElementById('profileEditBox');
  if(box) box.focus();
}
function cancelProfileEdit(){
  editingProfile = false;
  renderProfileFit();
}
async function saveProfileEdit(){
  const box = document.getElementById('profileEditBox');
  const text = box ? box.value.trim() : '';
  if(!text){ cancelProfileEdit(); return; }
  const statusEl = document.getElementById('profileEditStatus');
  const saveBtn = document.getElementById('profileEditSaveBtn');
  if(saveBtn) saveBtn.disabled = true;
  if(statusEl) statusEl.textContent = 'Updating your profile…';
  await mergeIntoProfile(text);
  editingProfile = false;
  renderProfileFit();
}

// Jumps to the Finder Suggest flow with the current profile already in place —
// used by empty-profile prompts elsewhere in the app.
function jumpToProfile(){
  showPage('home');
}

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
    el.classList.add('opacity-70');
    el.innerHTML = `
      <div>
        <h3 class="font-heading font-bold text-xl mb-2">Suggest opportunities for me</h3>
        <p class="text-sm text-slate-600">Based on everything in your profile. Add a few things to your profile first and this option unlocks.</p>
      </div>
      <button class="mt-4 pop-btn bg-white text-slate-900 font-bold px-4 py-2 rounded-xl w-full" onclick="showPage('home')">Go to Your Profile →</button>
    `;
    return;
  }
  el.classList.remove('opacity-70');
  const preview = studentProfile.synthesized.length > 110 ? studentProfile.synthesized.slice(0, 110) + '…' : studentProfile.synthesized;
  el.innerHTML = `
    <div>
      <h3 class="font-heading font-bold text-xl mb-2">Suggest opportunities for me</h3>
      <p class="text-sm text-slate-600 mb-2">Skip straight to matches based on your profile.</p>
      <p class="text-xs text-indigo-700 font-medium italic border-l-2 border-indigo-300 pl-2">"${escapeHtmlTracker(preview)}"</p>
    </div>
    <button class="mt-4 pop-btn bg-indigo-500 text-white font-bold px-4 py-2 rounded-xl w-full" onclick="startProfileSuggest()">Suggest opportunities →</button>
  `;
}

let suggestPendingQuestions = [];
let suggestAssessedKinds = [];
async function startProfileSuggest(){
  if(!studentProfile.synthesized) return;
  goStage('suggest');
  document.getElementById('suggestProfileSummary').innerHTML = `<div class="profile-fit-card"><p class="profile-entry-text">${escapeHtmlTracker(studentProfile.synthesized)}</p></div>`;
  document.getElementById('suggestQuestionsWrap').innerHTML = '';
  document.getElementById('suggestContinueBtn').style.display = 'none';
  document.getElementById('suggestError').classList.remove('show');
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
        merged.push({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','good','look'].includes(r.tier) ? r.tier : 'good', kind });
      });
    }
    if(!merged.length){
      throw new Error('No matches came back — try adding more detail to your profile, or browse by type instead.');
    }
    currentResults = merged;
    selectedIds = new Set();
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

loadProfile().then(() => { renderProfile(); renderSuggestEntryCard(); });

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
  // Prior sessions' synthesized profile, if any, adds continuity to the ranking.
  if(studentProfile.synthesized) prefsParts.push(`previously expressed interest (from past visits): ${studentProfile.synthesized}`);
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
        .map(r => ({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','good','look'].includes(r.tier) ? r.tier : 'good' }));
    }

    if(!currentResults.length){
      throw new Error('No matches came back — try adding more specific detail to your description.');
    }

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
const TIER_LABEL = { strong: 'Strong fit', good: 'Good fit', look: 'Worth a look' };
const TIER_CLASS = { strong: 'tier-strong', good: 'tier-good', look: 'tier-look' };
const TIER_ORDER = { strong: 0, good: 1, look: 2 };

function resultCardHTML(r){
  const o = r.opp;
  const isSelected = selectedIds.has(o.id);
  const metaParts = [o.org, o.type, o.price, o.location, o.state && o.state !== 'All States' ? o.state : null, o.season].filter(Boolean);
  const kindBadge = r.kind ? KIND_CONFIG[r.kind].name : (o.type || 'Opportunity');
  const bgClass = r.tier === 'strong' ? 'bg-emerald-50' : 'bg-white';
  
  return `
    <div class="pop-card ${bgClass} rounded-3xl p-5 sm:p-6 space-y-4 ${isSelected ? 'border-4 border-lime-400 bg-lime-50' : 'border-4 border-slate-900'}" id="result-${o.id}">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
         <div class="flex flex-wrap gap-2">
            <span class="bg-violet-200 text-violet-900 border-2 border-slate-900 font-bold text-[10px] uppercase px-3 py-1 rounded-full">${kindBadge}</span>
            ${r.tier === 'strong' ? `<span class="bg-yellow-300 border-2 border-slate-900 font-extrabold text-[10px] uppercase px-3 py-1 rounded-full">⭐ Strong Fit</span>` : ''}
         </div>
         <button class="pop-btn font-extrabold text-xs px-4 py-2 rounded-xl flex items-center justify-center gap-2 ${isSelected ? 'bg-lime-400 text-slate-900' : 'bg-white text-slate-900'}" onclick="toggleSelect('${o.id}')">
            ${isSelected ? '⭐ Saved Match' : '⭐ Save Match'}
         </button>
      </div>
      <div>
        <h3 class="font-heading text-xl sm:text-2xl font-bold text-slate-900"><a href="${o.url}" target="_blank" class="hover:underline">${o.name}</a></h3>
      </div>
      <div class="flex flex-wrap gap-2 text-xs font-bold">
         ${metaParts.map(m => `<span class="bg-slate-100 border border-slate-900 px-2.5 py-1 rounded-md">${m}</span>`).join('')}
      </div>
      ${r.reason ? `<div class="bg-slate-50 border-2 border-slate-200 p-3 rounded-2xl"><p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Why it fits</p><p class="text-xs text-slate-700 font-medium">${r.reason}</p></div>` : ''}
      ${o.summary ? `<p class="text-slate-600 text-sm leading-relaxed cursor-pointer line-clamp-3" onclick="this.classList.toggle('line-clamp-3')">${o.summary}</p>` : ''}
    </div>
  `;
}
function renderResults(){
  const sorted = [...currentResults].sort((a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier]);
  document.getElementById('resultGrid').innerHTML = sorted.map(resultCardHTML).join('');
  document.getElementById('resultsCount').textContent = `${currentResults.length} matches found`;
  updateSelectionBar();
}
function toggleSelect(id){
  if(selectedIds.has(id)) selectedIds.delete(id); else selectedIds.add(id);
  const card = document.getElementById('result-' + id);
  const btn = card.querySelector('.select-toggle');
  const isSelected = selectedIds.has(id);
  card.classList.toggle('selected', isSelected);
  btn.classList.toggle('selected', isSelected);
  btn.textContent = isSelected ? '✓ Selected' : 'Select';
  updateSelectionBar();
}
function updateSelectionBar(){
  const bar = document.getElementById('selectionBar');
  const count = selectedIds.size;
  document.getElementById('selectionCount').textContent = `${count} selected`;
  bar.style.display = currentResults.length ? 'flex' : 'none';
  document.getElementById('buildTrackerBtn').disabled = count === 0;
}

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

Date reasoning:
- If every deadline you found has already passed relative to today, and the program appears to run on a regular annual/recurring cycle, ESTIMATE next cycle's dates from the prior cycle's timing (e.g. last deadline was March 15 2025, program is annual → estimate a 2026 date near March 15, or later if you can't confirm the exact next date). Set was_estimated to true and say what it's based on in note (e.g. "Estimated from the 2025 cycle; 2027 dates not yet posted").
- Only mark status "running" if you found real evidence the program is currently active or has a future confirmed/estimated date. Use "unknown" if you found genuinely nothing usable after searching both the URL and the base site.
- Never invent a specific date with no basis — every date must come from something you actually found, whether confirmed or reasonably estimated from a real prior cycle.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching exactly this schema: {"status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format, separated by ' · '","fit":"one sentence, under 25 words, on what this actually involves","note":"one sentence, under 25 words: status/estimate basis/caveat","noteType":"good, plain, or flag — use flag if not_running or a major caveat","opens_iso":"YYYY-MM-DD when applications open, or null if unknown","deadlines":[{"label":"short specific label, e.g. 'Early Bird Registration'","date_iso":"YYYY-MM-DD"}],"deadline_label":"short text like ROLLING or TBA — only used when the deadlines array is empty","was_estimated":true or false,"requirements":[{"date":"short date text","text":"under 12 words — what's needed, not a repeat of a deadlines entry"}],"apply_url":"the best URL for actually applying","apply_label":"short button label like 'Apply now'","calendar_events":[{"date":"YYYY-MM-DD","text":"under 8 words","type":"deadline, opens, notify, or conference"}]}. Stay well within a 1000-token response: at most 3 deadlines entries, 3 requirements items, and 3 calendar_events. Never truncate mid-value or leave the JSON unclosed — shorten or drop optional arrays first, but keep at least the earliest deadline if one exists.`;
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
  pureCompetitions: 'Pure Competition',
  conferences: 'Conference',
  journals: 'Research Journal'
};

let trackerData = { summerPrograms: [], internships: [], researchCompetitions: [], pureCompetitions: [], conferences: [], journals: [] };
let trackerSavedState = {};
let trackerTypeFilter = new Set(ALL_BUCKETS); // which opportunity types show in the flat list — all by default

// ---------- Persistence ----------
async function loadTrackerData(){
  try{
    if(window.storage){
      const r = await window.storage.get('hs-tracker-data');
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
      }
    }
  }catch(e){ /* nothing saved yet, or storage unavailable — start fresh */ }
}
async function saveTrackerData(){
  try{ if(window.storage) await window.storage.set('hs-tracker-data', JSON.stringify(trackerData)); }
  catch(e){ /* storage unavailable — stays in-memory only for this session */ }
}
async function loadTrackerSaved(){
  try{
    if(window.storage){
      const r = await window.storage.get('hs-tracker-saved');
      if(r && r.value){ trackerSavedState = JSON.parse(r.value); }
    }
  }catch(e){}
}
async function saveTrackerSaved(){
  try{ if(window.storage) await window.storage.set('hs-tracker-saved', JSON.stringify(trackerSavedState)); }catch(e){}
}

// ---------- Page switching (Finder wizard <-> persistent Tracker) ----------
function showPage(name){
  ['home','wizard','tracker'].forEach(p => {
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

  const activeColors = { home: 'bg-yellow-300', wizard: 'bg-lime-300', tracker: 'bg-white' }; // Inspiration uses bg-white for all except Home initially, but active gets highlighted. Let's stick to simple:
  // Tracker btn is #navTrackerBtn
  
  ['home','wizard','tracker'].forEach(p => {
    const btnId = 'nav' + p.charAt(0).toUpperCase() + p.slice(1) + 'Btn';
    const btn = document.getElementById(btnId);
    if(btn) {
      if(p === name) {
        if(p === 'home') { btn.classList.add('bg-yellow-300'); btn.classList.remove('bg-white'); }
        if(p === 'wizard') { btn.classList.add('bg-lime-300'); btn.classList.remove('bg-white'); }
        if(p === 'tracker') { btn.classList.add('bg-indigo-300'); btn.classList.remove('bg-white'); }
      } else {
        btn.classList.add('bg-white');
        btn.classList.remove('bg-yellow-300', 'bg-lime-300', 'bg-indigo-300');
      }
    }
  });

  if(name === 'tracker'){ renderTrackerPage(); }
  if(name === 'home'){ renderHomePage(); }
  if(name === 'wizard'){ renderSuggestEntryCard(); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
function jumpToKind(kind){
  showPage('wizard');
  selectKind(kind);
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
function updateOppViewUI(){
  document.getElementById('oppViewCalendar').style.display = trackerOppView === 'calendar' ? '' : 'none';
  document.getElementById('oppViewList').style.display = trackerOppView === 'list' ? '' : 'none';
  document.getElementById('oppViewCalendarBtn').classList.toggle('active', trackerOppView === 'calendar');
  document.getElementById('oppViewListBtn').classList.toggle('active', trackerOppView === 'list');
}

// Non-exclusive type filter for the flat opportunities list — any combination of types
// can be shown at once, unlike the old exclusive per-type tabs.
function renderTypeFilterRow(){
  const row = document.getElementById('typeFilterRow');
  if(!row) return;
  row.innerHTML = ALL_BUCKETS.map(b => `
    <button class="bucket-pill${trackerTypeFilter.has(b) ? ' active' : ''}" onclick="toggleTypeFilter('${b}')">${BUCKET_LABELS[b]}</button>
  `).join('');
}
function toggleTypeFilter(bucket){
  if(trackerTypeFilter.has(bucket)) trackerTypeFilter.delete(bucket);
  else trackerTypeFilter.add(bucket);
  renderTrackerPage();
}
function resetTypeFilter(){
  trackerTypeFilter = new Set(ALL_BUCKETS);
  renderTrackerPage();
}

function goToTrackerCard(id){
  if(!id) return;
  if(trackerSavedState[id]){
    const drawer = document.getElementById('savedDrawer');
    if(drawer) drawer.open = true;
  }else{
    let targetBucket = null;
    for(const bucket of ALL_BUCKETS){
      if(trackerData[bucket].some(i => i.id === id)){ targetBucket = bucket; break; }
    }
    if(!targetBucket) return;
    if(!trackerTypeFilter.has(targetBucket)){
      trackerTypeFilter.add(targetBucket);
      renderTrackerPage();
    }
    setOppView('list');
  }
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
function trackerBadge(item){
  if(item.status === 'not_running'){
    return {cls:'notrunning-b', top:'NOT', bottom:'RUNNING'};
  }
  const next = earliestUpcoming(item);
  if(!next){
    return {cls:'rolling-b', top:item.deadlineLabel || 'ROLLING', bottom:''};
  }
  const days = daysUntil(next.date);
  const top = shortDate(next.date);
  const estSuffix = item.wasEstimated ? ' (est.)' : '';
  const isGenericLabel = !next.label || /^deadline$/i.test(next.label);
  const labelSuffix = next.kind === 'opens' ? ' · opens' : (isGenericLabel ? '' : ' · ' + next.label);
  if(days < 0) return {cls:'rolling-b', top:top, bottom:'passed'};
  if(days <= 14) return {cls:'urgent-b', top:top, bottom: days + ' days' + labelSuffix + estSuffix};
  if(days <= 45) return {cls:'soon-b', top:top, bottom: days + ' days' + labelSuffix + estSuffix};
  return {cls:'later-b', top:top, bottom: days + ' days' + labelSuffix + estSuffix};
}
function hashColor(seed){
  let hash = 0;
  for(let i = 0; i < seed.length; i++){ hash = seed.charCodeAt(i) + ((hash << 5) - hash); }
  const hue = Math.abs(hash) % 360;
  return { bg:`hsl(${hue}, 38%, 90%)`, border:`hsl(${hue}, 42%, 38%)`, text:`hsl(${hue}, 45%, 22%)` };
}
const MONTH_NAMES = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];

// ---------- Card rendering ----------
function toggleTrackerSaved(id){
  trackerSavedState[id] = !trackerSavedState[id];
  saveTrackerSaved();
  renderTrackerPage();
}
function findTrackedItem(id){
  for(const bucket of ALL_BUCKETS){
    const found = trackerData[bucket].find(i => i.id === id);
    if(found) return found;
  }
  return null;
}
function setProgressStatus(id, status){
  const item = findTrackedItem(id);
  if(!item) return;
  item.progressStatus = status;
  saveTrackerData();
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
  const b = trackerBadge(item);
  const notRunningBadge = item.status === 'not_running'
    ? `<span class="bg-rose-100 text-rose-900 border border-slate-900 font-bold text-[10px] uppercase px-2 py-0.5 rounded-full">Not running</span>`
    : '';
  const sourceBadge = sourceLabel ? `<span class="bg-indigo-100 text-indigo-900 border border-slate-900 font-bold text-[10px] uppercase px-2 py-0.5 rounded-full">${sourceLabel}</span>` : '';
  const estimatedNote = item.wasEstimated && item.status !== 'not_running'
    ? `<p class="text-xs text-amber-700 bg-amber-50 p-2 rounded-lg border border-amber-200">Predicted dates from past cycle.</p>`
    : '';
  const deadlineRows = (item.deadlines && item.deadlines.length)
    ? `<div class="space-y-1 mt-2">
         ${item.deadlines.map(d => `<div class="flex items-center gap-2 text-xs font-medium text-slate-700"><span class="bg-slate-200 px-1.5 rounded">${shortDate(d.dateISO)}</span> ${d.label}</div>`).join('')}
       </div>`
    : '';
  const isSaved = !!trackerSavedState[item.id];
  const progress = item.progressStatus || 'not_started';
  
  // Progress Bar for Kanban Style
  let progColor = 'bg-slate-200'; let progW = 'w-0'; let progText = 'Not Started';
  if (progress === 'in_progress') { progColor = 'bg-indigo-500'; progW = 'w-1/2'; progText = 'In Progress'; }
  if (progress === 'completed') { progColor = 'bg-emerald-500'; progW = 'w-full'; progText = 'Completed'; }

  return `
    <div class="pop-card bg-white p-4 rounded-2xl space-y-3 ${item.status === 'not_running' ? 'opacity-60' : ''}" id="tracker-card-${item.id}">
      <div class="flex justify-between items-start gap-2">
        <div class="flex flex-wrap gap-1">
          ${item.type ? `<span class="bg-purple-200 text-purple-900 border border-slate-900 font-bold text-[10px] uppercase px-2 py-0.5 rounded-full">${item.type}</span>` : ''}
          ${notRunningBadge} ${sourceBadge}
        </div>
        <div class="flex items-center gap-1">
          <button onclick="event.stopPropagation(); toggleTrackerSaved('${item.id}')" class="text-lg hover:scale-110 transition-transform" title="${isSaved ? 'Restore' : 'Save'}">${isSaved ? '★' : '☆'}</button>
          <button onclick="event.stopPropagation(); deleteTrackerItem('${item.id}', this)" class="text-xs font-bold text-slate-400 hover:text-rose-600 transition-colors" title="Delete">✕</button>
        </div>
      </div>
      
      <div>
        <h4 class="font-bold text-sm text-slate-900 leading-tight"><a href="${item.url}" target="_blank" class="hover:underline">${item.name}</a></h4>
        <p class="text-xs text-slate-500 mt-0.5 line-clamp-1">${item.meta || ''}</p>
      </div>

      ${estimatedNote}
      ${deadlineRows}
      
      <details class="text-xs text-slate-500 cursor-pointer marker:text-indigo-500">
        <summary class="font-bold hover:text-indigo-600">Show details</summary>
        <div class="mt-2 bg-slate-50 p-2 rounded-lg border border-slate-200">
          <p class="mb-1">${item.fit}</p>
          ${item.requirements ? item.requirements.map(r => `<div class="flex gap-2 mb-1"><span class="font-bold">${r.date}</span><span>${r.text}</span></div>`).join('') : ''}
          <p class="italic text-[10px] mt-1">${item.note}</p>
        </div>
      </details>

      <div class="w-full bg-slate-200 h-2 rounded-full overflow-hidden border border-slate-900 cursor-pointer relative group" title="Click to cycle status" onclick="cycleProgressStatus('${item.id}')">
        <div class="${progColor} h-full ${progW} transition-all"></div>
      </div>
      
      <div class="flex justify-between items-center text-xs font-bold pt-1 border-t border-slate-100">
        <span class="text-slate-500 cursor-pointer hover:text-slate-900" onclick="cycleProgressStatus('${item.id}')">${progText} (Click to change)</span>
        <a href="${item.applyUrl}" target="_blank" class="text-indigo-600 hover:underline bg-indigo-50 px-2 py-1 rounded-md">${item.applyLabel}</a>
      </div>
    </div>
  `;
}

// Quick helper to cycle progress on click for the new UI
window.cycleProgressStatus = function(id) {
  const item = Object.values(trackerData).flat().find(i => i.id === id);
  if(!item) return;
  const p = item.progressStatus || 'not_started';
  const next = p === 'not_started' ? 'in_progress' : (p === 'in_progress' ? 'completed' : 'not_started');
  setProgressStatus(id, next);
  if(next === 'completed' && typeof confetti === 'function') confetti({particleCount: 50, spread: 60, origin: {y: 0.8}});
};
function sortedByTrackerDeadline(list){
  const earliestDateOnly = (item) => {
    const next = earliestUpcoming(item);
    return next ? next.date : '9999-12-31';
  };
  return [...list].sort((a, b) => {
    if(a.status === 'not_running' && b.status !== 'not_running') return 1;
    if(b.status === 'not_running' && a.status !== 'not_running') return -1;
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
function renderCalendarStripGeneric(dates, stripId, legendId){
  const byMonth = {};
  dates.forEach(d => { const ym = d.date.slice(0,7); (byMonth[ym] = byMonth[ym] || []).push(d); });
  const months = Object.keys(byMonth).sort();
  const strip = document.getElementById(stripId);
  const legend = document.getElementById(legendId);
  if(!months.length){
    strip.innerHTML = '<p class="empty-state">Nothing on this calendar yet.</p>';
    legend.innerHTML = '';
    return;
  }
  const now = new Date();
  const currentYM = now.toISOString().slice(0,7);
  strip.innerHTML = months.map(ym => {
    const [y, m] = ym.split('-');
    const entries = byMonth[ym].sort((a,b) => a.date.localeCompare(b.date));
    const isCurrent = ym === currentYM;
    return `
      <div class="month-card${isCurrent ? ' current-month' : ''}">
        <div class="month-head">${MONTH_NAMES[parseInt(m,10)-1]} ${y}</div>
        <div class="month-entries">
          ${entries.map(e => {
            const c = hashColor(e.label);
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
  }).join('');
  const presentLabels = [...new Set(dates.map(d => d.label))];
  legend.innerHTML = presentLabels.map(name => `
    <span><span class="legend-dot" style="background:${hashColor(name).border};"></span>${name}</span>
  `).join('');
}
function renderAllTrackerCalendars(){
  const pubItems = [...trackerData.conferences, ...trackerData.journals].filter(i => !trackerSavedState[i.id]);
  const compItems = [...trackerData.researchCompetitions, ...trackerData.pureCompetitions].filter(i => !trackerSavedState[i.id]);
  const summerItems = [...trackerData.summerPrograms, ...trackerData.internships].filter(i => !trackerSavedState[i.id]);
  renderCalendarStripGeneric(deriveKeyDatesForItems(pubItems), 'calendarStrip', 'calendarLegend');
  renderCalendarStripGeneric(deriveKeyDatesForItems(compItems), 'competitionsCalendarStrip', 'competitionsCalendarLegend');
  renderCalendarStripGeneric(deriveKeyDatesForItems(summerItems), 'summerCalendarStrip', 'summerCalendarLegend');
}

// ---------- Full page render ----------
function updateTrackerNavCount(){
  const total = ALL_BUCKETS.reduce((s, b) => s + trackerData[b].filter(i => !trackerSavedState[i.id]).length, 0);
  document.getElementById('trackerNavCount').textContent = total ? `(${total})` : '';
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
      const p = item.progressStatus || 'not_started';
      if(stats[p] !== undefined) stats[p]++;
    });
  });
  return stats;
}
function renderStats(){
  const s = computeStats();
  document.getElementById('statTotal').textContent = s.total;
  document.getElementById('statNotStarted').textContent = s.not_started;
  document.getElementById('statInProgress').textContent = s.in_progress;
  document.getElementById('statCompleted').textContent = s.completed;
  document.getElementById('statDueSoon').textContent = getUpcomingDeadlineItems().length;
}

// ---------- Home page: single synthesized profile card ----------
function renderProfileFit(){
  const wrap = document.getElementById('profileFitSection');
  if(!wrap) return;

  if(editingProfile){
    wrap.innerHTML = `
      <div class="space-y-3">
        <textarea class="w-full border-2 border-slate-900 rounded-xl p-4 text-sm font-medium focus:outline-none focus:shadow-[2px_2px_0px_#0F172A]" id="profileEditBox" rows="4" placeholder="Add anything new..."></textarea>
        <div class="flex gap-2">
          <button class="pop-btn bg-lime-300 text-slate-900 font-bold px-4 py-2 rounded-xl" id="profileEditSaveBtn" onclick="saveProfileEdit()">Save Profile</button>
          <button class="pop-btn bg-white text-slate-900 font-bold px-4 py-2 rounded-xl" onclick="cancelProfileEdit()">Cancel</button>
        </div>
        <p class="text-xs font-bold text-slate-500" id="profileEditStatus"></p>
      </div>
    `;
    return;
  }

  if(!studentProfile.synthesized){
    wrap.innerHTML = `
      <div class="bg-slate-50 border-2 border-slate-200 border-dashed rounded-2xl p-6 text-center">
        <p class="text-sm font-bold text-slate-400 mb-4">Nothing here yet — describe yourself in the Finder or add it directly.</p>
        <button class="pop-btn bg-white text-slate-900 font-bold px-4 py-2 rounded-xl text-sm" onclick="startProfileEdit()">+ Add to profile</button>
      </div>
    `;
    return;
  }

  wrap.innerHTML = `
    <div class="bg-indigo-50 border-2 border-slate-900 rounded-2xl p-4 sm:p-6">
      <p class="text-sm text-slate-700 leading-relaxed font-medium mb-4">${escapeHtmlTracker(studentProfile.synthesized)}</p>
      <div class="flex gap-3 pt-4 border-t-2 border-indigo-200">
        <button class="text-xs font-bold text-indigo-700 hover:underline" onclick="startProfileEdit()">✎ Edit</button>
        <button class="text-xs font-bold text-rose-600 hover:underline" onclick="clearProfile(this)">🗑 Delete</button>
      </div>
    </div>
  `;
}

// ---------- Deadlines due this month & next ----------
function getUpcomingDeadlineItems(){
  const now = new Date();
  const thisMonthKey = now.getFullYear() * 12 + now.getMonth();
  const nextMonthKey = thisMonthKey + 1;
  const results = [];
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].forEach(item => {
      if(item.status === 'not_running') return;
      if(trackerSavedState[item.id]) return;
      const next = earliestUpcoming(item);
      if(!next) return;
      const d = new Date(next.date + 'T00:00:00');
      const key = d.getFullYear() * 12 + d.getMonth();
      if(key === thisMonthKey || key === nextMonthKey){
        results.push({ item, bucket, nextDate: next.date, nextLabel: next.label, nextKind: next.kind });
      }
    });
  });
  results.sort((a, b) => a.nextDate.localeCompare(b.nextDate));
  return results;
}
// ---------- Full Home page render ----------
function renderHomePage(){
  if(!document.getElementById('statTotal')) return; // page not in DOM yet
  renderStats();
  renderProfileFit();
}

function renderTrackerPage(){
  // Flat, filterable list of everything actively tracked (not saved-for-later), across
  // all opportunity types at once — sorted by nearest deadline, each card tagged with
  // its type since there's no longer a separate tab per type.
  const visibleItems = [];
  ALL_BUCKETS.forEach(bucket => {
    if(!trackerTypeFilter.has(bucket)) return;
    trackerData[bucket].filter(i => !trackerSavedState[i.id]).forEach(i => visibleItems.push({ item: i, bucket }));
  });
  const sortedItems = sortedByTrackerDeadline(visibleItems.map(x => x.item));
  const bucketByItemId = {};
  visibleItems.forEach(x => { bucketByItemId[x.item.id] = x.bucket; });
  document.getElementById('allOppCards').innerHTML = sortedItems.length
    ? sortedItems.map(i => trackerCardHTML(i, BUCKET_LABELS[bucketByItemId[i.id]])).join('')
    : (trackerTypeFilter.size === 0
        ? '<p class="empty-state">No types selected. <a href="#" onclick="event.preventDefault(); resetTypeFilter();">Show all</a></p>'
        : '<p class="empty-state">Nothing tracked here yet — add opportunities via the Finder or the button above.</p>');
  document.getElementById('allOppCount').textContent = String(sortedItems.length).padStart(2, '0');
  renderTypeFilterRow();

  const savedEntries = [];
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].filter(i => trackerSavedState[i.id]).forEach(i => savedEntries.push({ item: i, label: BUCKET_LABELS[bucket] }));
  });
  const savedSortedItems = sortedByTrackerDeadline(savedEntries.map(s => s.item));
  document.getElementById('savedCards').innerHTML = savedSortedItems.length
    ? savedSortedItems.map(i => trackerCardHTML(i, savedEntries.find(s => s.item.id === i.id).label)).join('')
    : '<p class="empty-state">Nothing saved yet — click "☆ Save for later" on any card to move it here.</p>';
  document.getElementById('savedDrawerCount').textContent = String(savedSortedItems.length).padStart(2, '0');

  renderAllTrackerCalendars();
  updateTrackerNavCount();
  renderHomePage();
}

// ---------- Build/reconcile from the wizard's selected results ----------
async function buildTracker(){
  const btn = document.getElementById('buildTrackerBtn');
  const label = document.getElementById('buildTrackerLabel');
  btn.disabled = true;
  btn.classList.add('loading');

  // Group selected opportunities by their bucket — the profile-based Suggest flow can
  // return results spanning several opportunity kinds at once (each result carries its
  // own r.kind), unlike the single-kind Type flow, which falls back to selectedKind.
  const selectedResults = currentResults.filter(r => selectedIds.has(r.opp.id));
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
    const existingIds = new Set(trackerData[bucket].map(i => i.id));
    const existingUrls = new Set(trackerData[bucket].map(i => i.url));
    const toFetch = opps.filter(o => !existingIds.has(o.id) && !existingUrls.has(o.url));
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
          applyLabel: info.apply_label || 'Apply / learn more'
        });
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
          requirements: null, applyUrl: opp.url, applyLabel: 'Visit site'
        });
      }
    }
  }

  await saveTrackerData();

  btn.disabled = false;
  btn.classList.remove('loading');
  label.textContent = 'Add to my tracker →';

  buckets.forEach(b => trackerTypeFilter.add(b));
  showPage('tracker');
  setOppView('list');

  const changeBanner = document.getElementById('trackerChangeBanner');
  if(totalToFetch){
    const scopeText = buckets.length > 1
      ? `across ${buckets.length} categories`
      : `to ${BUCKET_LABELS[buckets[0]]}s`;
    changeBanner.innerHTML = `<strong>Added ${totalToFetch} new opportunit${totalToFetch === 1 ? 'y' : 'ies'}</strong> ${scopeText}.` +
      (totalAlready ? ` ${totalAlready} of your selections ${totalAlready === 1 ? 'was' : 'were'} already being tracked, so ${totalAlready === 1 ? 'it was' : 'those were'} left untouched.` : '');
  }else{
    changeBanner.innerHTML = `All ${totalAlready} selected opportunit${totalAlready === 1 ? 'y is' : 'ies are'} already in your tracker — nothing new to add.`;
  }
  changeBanner.classList.add('show');
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
    changeBanner.innerHTML = `<strong>${changes.length} item${changes.length > 1 ? 's' : ''} updated:</strong><ul>${changes.map(c => `<li>${c}</li>`).join('')}</ul>`;
    changeBanner.classList.add('show');
  }
  if(failures){
    errorBanner.textContent = failures === allItems.length
      ? `Couldn't reach the Claude API from this page. Live refresh only works when this file is opened through a Claude-connected environment.`
      : `${failures} item${failures > 1 ? 's' : ''} couldn't be checked (site may be blocking automated access). Left unchanged.`;
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

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","opens_iso":"YYYY-MM-DD or null","deadlines":[{"label":"short label","date_iso":"YYYY-MM-DD"}],"deadline_label":"short text like ROLLING, only if deadlines is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null"}. Stay well within 1000 tokens: at most 3 deadlines and 3 requirements.`;
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
      applyLabel: extracted.apply_label || 'Apply / learn more'
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
    label.textContent = 'Analyze & add';
  }
}

// ---------- To Do (persistent, scoped to the Tracker page) ----------
function escapeHtmlTracker(str){
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

// ---------- Initial load ----------
Promise.all([loadTrackerData(), loadTrackerSaved()]).then(() => {
  updateTrackerNavCount();
  renderTrackerPage();
});