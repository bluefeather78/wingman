// Opportunities database is loaded asynchronously from Supabase, via the
// server-side proxy/cache at /api/opportunities (see server.py).
let OPPORTUNITIES = [];
fetch('/api/opportunities')
  .then(res => res.json())
  .then(data => { OPPORTUNITIES = Array.isArray(data) ? data : []; })
  .catch(err => console.error('Failed to load /api/opportunities:', err));

// ============================================================
// Auth — plain userid/password sign-in + registration. Accounts are persisted
// server-side in a JSON file database (see server.py: /api/register, /api/login,
// users_db.json) so an account, once created, survives page reloads and works
// from any browser hitting this server — not just a client-side cache.
// Passwords are hashed with SHA-256 client-side before ever leaving the browser;
// the server only ever sees/stores the hash. Reasonable for a prototype, but not
// production-grade (no salting, no HTTPS enforcement, no rate limiting).
// ============================================================

let currentUser = null; // { userid, firstName, lastName, email, location } — the signed-in session, cached locally
let googlePendingToken = null; // set while #googleFinishForm is showing — see handleGoogleRedirect()

// Query-string userid for the endpoints that cost money but take no JSON body we can put
// it in (the deadline check is a GET, the resume import is multipart). It is only used
// server-side to attribute spend to an account in the admin console's per-user cost card
// — signed-out calls just omit it and show up there as unattributed.
function costAttributionQS(){
  const id = currentUser && currentUser.userid;
  return id ? `?userid=${encodeURIComponent(id)}` : '';
}


// App state for tracking data sync status (deadlines, etc.)
let appState = {
  lastDeadlineSync: null,      // ISO timestamp of last successful deadline sync
  syncInProgress: false,        // Boolean flag while sync is running
  deadlineSyncError: null       // Error message if last sync failed
};

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

// Syncs deadlines for all tracked opportunities from the server cache. Runs in the
// background after login without blocking the UI. Updates tracker items with fresh
// deadline data and persists. Tracks sync state (timestamp, error) for UI indicators.
async function syncTrackerDeadlines(){
  if(appState.syncInProgress) return; // Avoid duplicate syncs
  appState.syncInProgress = true;
  appState.deadlineSyncError = null;

  try{
    // Collect all opportunity IDs across all tracker buckets
    const allIds = [];
    Object.values(trackerData).forEach(bucket => {
      if(Array.isArray(bucket)){
        bucket.forEach(item => { if(item.id) allIds.push(item.id); });
      }
    });

    if(!allIds.length){
      appState.syncInProgress = false;
      return;
    }

    // Fetch deadline data for each opportunity (uses server 7-day cache)
    const deadlinePromises = allIds.map(id =>
      fetch(`/api/opportunities/${id}/deadline${costAttributionQS()}`)
        .then(res => res.ok ? res.json() : null)
        .catch(() => null)
    );

    const results = await Promise.all(deadlinePromises);

    // Apply updates to tracker items
    let updatedCount = 0;
    const buckets = Object.values(trackerData);
    for(let i = 0; i < allIds.length; i++){
      const id = allIds[i];
      const deadlineData = results[i];
      if(!deadlineData) continue;

      // Find and update the item across all buckets
      for(const bucket of buckets){
        if(!Array.isArray(bucket)) continue;
        const item = bucket.find(it => it.id === id);
        if(!item) continue;

        // Merge fresh deadline info into the item
        if(deadlineData.status) item.status = deadlineData.status;
        if(deadlineData.important_dates) item.importantDates = deadlineData.important_dates;
        if(deadlineData.important_date_note) item.note = deadlineData.important_date_note;
        if(deadlineData.was_estimated !== undefined) item.wasEstimated = deadlineData.was_estimated;
        if(deadlineData.dates_last_checked_at) item.datesLastCheckedAt = deadlineData.dates_last_checked_at;
        updatedCount++;
        break;
      }
    }

    // Persist updates and re-render if anything changed
    if(updatedCount > 0){
      await saveTrackerData();
      if(currentStage === 'tracker' || document.getElementById('page-tracker').classList.contains('active')){
        renderTrackerPage();
      }
    }

    // Mark sync as complete
    appState.lastDeadlineSync = new Date().toISOString();
    appState.syncInProgress = false;
  }catch(err){
    console.error('Deadline sync failed:', err);
    appState.deadlineSyncError = err.message;
    appState.syncInProgress = false;
  }
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

// Toggles between the Sign In, Register, and Google-finish forms on the login screen.
function showLoginMode(mode){
  const signInForm = document.getElementById('signInForm');
  const registerForm = document.getElementById('registerForm');
  const googleFinishForm = document.getElementById('googleFinishForm');
  const googleRow = document.getElementById('googleSignInRow');
  const tagline = document.getElementById('loginTagline');
  const signInError = document.getElementById('signInError');
  const registerError = document.getElementById('registerError');
  if(signInError) signInError.textContent = '';
  if(registerError) registerError.textContent = '';
  if(mode === 'google'){
    // Mid-signup consent step for a brand-new Google account — the other two forms and
    // the Google button itself are irrelevant until this one is finished or abandoned.
    if(signInForm) signInForm.classList.add('hidden');
    if(registerForm) registerForm.classList.add('hidden');
    if(googleRow) googleRow.classList.add('hidden');
    if(googleFinishForm) googleFinishForm.classList.remove('hidden');
    if(tagline) tagline.textContent = 'Just a few more details to finish creating your account.';
    return;
  }
  if(googleFinishForm) googleFinishForm.classList.add('hidden');
  if(googleRow) googleRow.classList.remove('hidden');
  if(mode === 'register'){
    if(signInForm) signInForm.classList.add('hidden');
    if(registerForm) registerForm.classList.remove('hidden');
    updateRegisterConsent();
    if(tagline) tagline.textContent = 'Create an account to find and track opportunities built around your projects.';
  }else{
    if(registerForm) registerForm.classList.add('hidden');
    if(signInForm) signInForm.classList.remove('hidden');
    if(tagline) tagline.textContent = 'Sign in to find and track opportunities built around your projects.';
  }
}

// The parental-permission checkbox only applies to under-18s, so it disappears the
// moment someone says they're 18+. Hiding it isn't enough on its own — a box that was
// ticked before the user corrected their age would still be ticked and would still be
// submitted, so clear it on the way out.
function updateRegisterConsent(){
  const isAdult = document.getElementById('regIsAdult');
  const parentalRow = document.getElementById('regParentalRow');
  const parental = document.getElementById('regParentalConsent');
  if(!isAdult || !parentalRow) return;
  if(isAdult.checked){
    parentalRow.classList.add('hidden');
    if(parental) parental.checked = false;
  }else{
    parentalRow.classList.remove('hidden');
  }
}

async function registerUser(event){
  event.preventDefault();
  const errorEl = document.getElementById('registerError');
  const firstName = document.getElementById('regFirstName').value.trim();
  const lastName = document.getElementById('regLastName').value.trim();
  const email = document.getElementById('regEmail').value.trim();
  const userid = document.getElementById('regUserid').value.trim();
  const location = document.getElementById('regLocation').value.trim();
  const password = document.getElementById('regPassword').value;
  const passwordConfirm = document.getElementById('regPasswordConfirm').value;
  const isAdult = !!document.getElementById('regIsAdult')?.checked;
  const parentalConsent = !!document.getElementById('regParentalConsent')?.checked;
  const acceptedTerms = !!document.getElementById('regAcceptedTerms')?.checked;

  if(!firstName || !lastName || !email || !userid || !location || !password || !passwordConfirm){
    if(errorEl) errorEl.textContent = 'Please fill in every field.';
    return;
  }
  // Shape check only. Whether the address is already taken is a question only the server
  // can answer, and handle_register() answers it for both the user ID and the email.
  if(!/^[^\s@,()'"]+@[^\s@,()'"]+\.[^\s@,()'"]{2,}$/.test(email)){
    if(errorEl) errorEl.textContent = 'Please enter a valid email address.';
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
  // Mirrors handle_register's server-side check; the server is what actually refuses
  // the account, this just says why without a round-trip.
  if(!isAdult && !parentalConsent){
    if(errorEl) errorEl.textContent = 'If you are under 18, confirm that a parent or guardian has given you permission.';
    return;
  }
  if(!acceptedTerms){
    if(errorEl) errorEl.textContent = 'Please read and accept the Terms of Use and Privacy Policy.';
    return;
  }

  const passwordHash = await hashPassword(password);
  let data;
  try{
    const res = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ firstName, lastName, email, userid, location, passwordHash,
                             isAdult, parentalConsent, acceptedTerms })
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

  currentUser = { userid, firstName, lastName, email, location };
  await saveUser();
  if(typeof firebase !== 'undefined' && firebase.analytics) {
    firebase.analytics().logEvent('user_registered', {
      'location': location
    });
  }
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

  currentUser = { userid, firstName: data.firstName, lastName: data.lastName, email: data.email, location: data.location || '' };
  // /api/login already returns the subscription block, so showApp() can decide between
  // the app and the paywall without waiting on a second request.
  if(data.subscription) currentUser.subscription = data.subscription;
  await saveUser();
  if(typeof firebase !== 'undefined' && firebase.analytics) {
    firebase.analytics().logEvent('user_login', {
      'location': data.location || 'unspecified'
    });
  }
  await showApp();
}

// Kicks off the redirect-based OAuth flow — see handle_google_start in server.py.
// Used by the "Continue with Google" button on both the sign-in and register forms.
function googleSignIn(){
  location.href = '/api/auth/google/start';
}

// The parental-permission checkbox for the Google finish form — mirrors
// updateRegisterConsent() for #registerForm.
function updateGoogleFinishConsent(){
  const isAdult = document.getElementById('googleIsAdult');
  const parentalRow = document.getElementById('googleParentalRow');
  const parental = document.getElementById('googleParentalConsent');
  if(!isAdult || !parentalRow) return;
  if(isAdult.checked){
    parentalRow.classList.add('hidden');
    if(parental) parental.checked = false;
  }else{
    parentalRow.classList.remove('hidden');
  }
}

// Called once on page load. handle_google_calendar_callback in server.py redirects back
// to `/?calendar_connected=1` after the user grants Calendar access — this just strips
// the marker param and reports whether it was there, so the init chain below can show a
// confirmation once the normal session-restore has run. Unlike handleGoogleRedirect()
// this carries no token: the tokens that matter (access/refresh) were already persisted
// server-side against the userid in the OAuth state, not handed to the client.
function checkCalendarConnectedRedirect(){
  const params = new URLSearchParams(location.search);
  if(!params.has('calendar_connected')) return false;
  params.delete('calendar_connected');
  const qs = params.toString();
  history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : ''));
  return true;
}

// Called once on page load. handle_google_callback in server.py redirects back to
// `/?google_token=...` after the user approves on Google's side; this resolves that
// one-time token into either a completed sign-in or a "finish creating your account"
// step. Returns true if it took over the login gate (so the caller skips the normal
// loadUser()-based landing/app decision), false otherwise.
async function handleGoogleRedirect(){
  const params = new URLSearchParams(location.search);
  const token = params.get('google_token');
  if(!token) return false;
  // Strip it from the URL immediately — it's single-use server-side, and leaving it
  // visible would just invite a confusing "expired link" on refresh.
  params.delete('google_token');
  const qs = params.toString();
  history.replaceState(null, '', location.pathname + (qs ? `?${qs}` : ''));

  let data;
  try{
    const res = await fetch(`/api/auth/google/session?token=${encodeURIComponent(token)}`);
    data = await res.json().catch(() => ({}));
    if(!res.ok){
      showLoginGate('signin');
      const errorEl = document.getElementById('signInError');
      if(errorEl) errorEl.textContent = data.error || 'Could not complete Google sign-in.';
      return true;
    }
  }catch(e){
    showLoginGate('signin');
    return true;
  }

  if(data.pending){
    googlePendingToken = token;
    showLoginGate('google');
    const nameEl = document.getElementById('googleFinishName');
    if(nameEl) nameEl.textContent = data.firstName || data.email;
    return true;
  }

  currentUser = { userid: data.userid, firstName: data.firstName, lastName: data.lastName, email: data.email, location: data.location || '' };
  if(data.subscription) currentUser.subscription = data.subscription;
  await saveUser();
  await showApp();
  return true;
}

// Submits the consent step for a brand-new Google account — see handle_google_finish
// in server.py. Mirrors registerUser()'s tail once the account is created.
async function finishGoogleSignup(event){
  event.preventDefault();
  const errorEl = document.getElementById('googleFinishError');
  const location_ = document.getElementById('googleFinishLocation').value.trim();
  const isAdult = !!document.getElementById('googleIsAdult')?.checked;
  const parentalConsent = !!document.getElementById('googleParentalConsent')?.checked;
  const acceptedTerms = !!document.getElementById('googleAcceptedTerms')?.checked;

  if(!googlePendingToken){
    if(errorEl) errorEl.textContent = 'This sign-in link has expired. Please try signing in with Google again.';
    return;
  }
  if(!location_){
    if(errorEl) errorEl.textContent = 'Please fill in every field.';
    return;
  }
  if(!isAdult && !parentalConsent){
    if(errorEl) errorEl.textContent = 'If you are under 18, confirm that a parent or guardian has given you permission.';
    return;
  }
  if(!acceptedTerms){
    if(errorEl) errorEl.textContent = 'Please read and accept the Terms of Use and Privacy Policy.';
    return;
  }

  let data;
  try{
    const res = await fetch('/api/auth/google/finish', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: googlePendingToken, location: location_, isAdult, parentalConsent, acceptedTerms })
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

  googlePendingToken = null;
  currentUser = { userid: data.userid, firstName: data.firstName, lastName: data.lastName, email: data.email, location: data.location || '' };
  if(data.subscription) currentUser.subscription = data.subscription;
  await saveUser();
  if(typeof firebase !== 'undefined' && firebase.analytics) {
    firebase.analytics().logEvent('user_registered', {
      'location': location_
    });
  }
  await showApp();
}

// The signed-out marketing page shown before the sign-in/register form. See
// showLoginGate() for the form itself.
function showLandingPage(){
  const landingPage = document.getElementById('page-landing');
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  const locked = document.getElementById('page-locked');
  if(landingPage) landingPage.classList.remove('hidden');
  if(loginPage) loginPage.classList.add('hidden');
  if(appShell) appShell.classList.add('hidden');
  if(locked) locked.classList.add('hidden');
}

function showLoginGate(mode){
  const landingPage = document.getElementById('page-landing');
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  const locked = document.getElementById('page-locked');
  if(landingPage) landingPage.classList.add('hidden');
  if(loginPage) loginPage.classList.remove('hidden');
  if(appShell) appShell.classList.add('hidden');
  if(locked) locked.classList.add('hidden');
  showLoginMode(mode === 'register' || mode === 'google' ? mode : 'signin');
}

// Signed in, but out of trial and unpaid. Replaces the app entirely rather than
// disabling pieces of it — see #page-locked in index.html. The server enforces the
// same thing on the endpoints that cost money (Handler._subscription_blocks), so
// this screen is the explanation, not the lock.
function showPaywall(){
  const landingPage = document.getElementById('page-landing');
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  const locked = document.getElementById('page-locked');
  if(landingPage) landingPage.classList.add('hidden');
  if(loginPage) loginPage.classList.add('hidden');
  if(appShell) appShell.classList.add('hidden');
  if(locked) locked.classList.remove('hidden');

  const sub = (currentUser && currentUser.subscription) || {};
  const title = document.getElementById('lockedTitle');
  const message = document.getElementById('lockedMessage');
  if(sub.status === 'canceled'){
    if(title) title.textContent = 'Your subscription has ended';
    if(message) message.textContent = 'Resubscribe to pick up where you left off — your profile and tracker are still here.';
  }else if(sub.status === 'beta'){
    if(title) title.textContent = 'Your beta access has ended';
    if(message) message.textContent = 'Subscribe to keep finding and tracking opportunities. Your profile and tracker are still here.';
  }else if(sub.status === 'past_due'){
    if(title) title.textContent = 'There was a problem with your payment';
    if(message) message.textContent = 'We could not charge your card. Update your payment details to restore access.';
  }else{
    if(title) title.textContent = 'Your free trial has ended';
    if(message) message.textContent = 'Subscribe to keep finding and tracking opportunities. Your profile and tracker are still here.';
  }
}

// True when the signed-in account may use the app. Absent subscription info means
// "not yet loaded", not "expired" — locking someone out because a status request
// failed would be worse than briefly letting them in, so this fails open.
// The server 402s the paid endpoints for a lapsed account. That can happen mid-session
// (the trial ran out while the tab was open), so treat it as authoritative: mark the
// subscription as lapsed locally and show the paywall, instead of surfacing a bare
// "API error 402" from whatever feature happened to fire first.
function handleSubscriptionLapsed(){
  if(!currentUser) return;
  currentUser.subscription = Object.assign({}, currentUser.subscription, { has_access: false });
  showPaywall();
}

function hasSubscriptionAccess(){
  if(!currentUser || !currentUser.subscription) return true;
  return currentUser.subscription.has_access !== false;
}
async function showApp(){
  // If the login response already told us the account is locked out, bail before the
  // app shell is ever unhidden — otherwise the user sees the full app flash past on
  // the way to the paywall.
  if(!hasSubscriptionAccess()){ showPaywall(); return; }

  const landingPage = document.getElementById('page-landing');
  const loginPage = document.getElementById('page-login');
  const appShell = document.getElementById('appShell');
  const locked = document.getElementById('page-locked');
  if(landingPage) landingPage.classList.add('hidden');
  if(loginPage) loginPage.classList.add('hidden');
  if(locked) locked.classList.add('hidden');
  if(appShell) appShell.classList.remove('hidden');

  const nameEl = document.getElementById('accountName');
  const emailEl = document.getElementById('accountEmail');
  const greetingEl = document.getElementById('homeGreetingName');
  const locationInputEl = document.getElementById('accountLocationInput');
  const fullName = [currentUser.firstName, currentUser.lastName].filter(Boolean).join(' ');
  if(nameEl) nameEl.textContent = fullName || currentUser.email;
  if(emailEl) emailEl.textContent = currentUser.email || '';
  if(greetingEl) greetingEl.textContent = currentUser.firstName || 'there';
  if(locationInputEl) locationInputEl.value = currentUser.location || '';

  // Profile/tracker data is scoped to this account (see AppStorage) — load it fresh
  // on every sign-in rather than trusting whatever's still sitting in memory from a
  // previous session in this tab.
  await loadAccountData();
  await checkSubscriptionStatus();
  if(!hasSubscriptionAccess()){ showPaywall(); return; }
  showPage('home');
  // Sync deadlines in background (non-blocking) for all tracked opportunities
  syncTrackerDeadlines();
}
async function logoutUser(){
  currentUser = null;
  await saveUser();
  // Clear in-memory app data so it can't leak into a different account that signs
  // in next in this same tab — the next login re-fetches everything fresh via
  // loadAccountData().
  studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0, filterValues: null, filterTags: null };
  trackerData = { summerPrograms: [], internships: [], researchCompetitions: [], pureCompetitions: [], conferences: [], journals: [] };
  trackerSavedState = {};
  toggleProfile(); // close the drawer on the way out
  showLandingPage();
}
async function saveAccountLocation(){
  const inputEl = document.getElementById('accountLocationInput');
  const statusEl = document.getElementById('accountLocationStatus');
  if(!inputEl || !currentUser) return;
  const location = inputEl.value.trim();
  if(!location){
    if(statusEl) statusEl.textContent = 'Location cannot be empty.';
    return;
  }
  try{
    const res = await fetch('/api/account/location', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid: currentUser.userid, location })
    });
    if(!res.ok) throw new Error('request failed');
    currentUser.location = location;
    await saveUser();
    if(statusEl){
      statusEl.textContent = 'Saved!';
      setTimeout(() => { if(statusEl.textContent === 'Saved!') statusEl.textContent = ''; }, 2000);
    }
  }catch(e){
    if(statusEl) statusEl.textContent = 'Could not save — please try again.';
  }
}

// ============================================================
// Subscription management
// ============================================================
async function checkSubscriptionStatus(){
  if(!currentUser) return;
  try{
    const res = await fetch('/api/subscription/status', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid: currentUser.userid })
    });
    if(!res.ok) return;
    const data = await res.json();
    currentUser.subscription = data;
    updateSubscriptionUI();
    // A trial can lapse while a tab is sitting open. If the refreshed status says the
    // account no longer has access, swap to the paywall rather than leaving a live app
    // on screen whose every AI action would now come back 402.
    const appShell = document.getElementById('appShell');
    if(!hasSubscriptionAccess() && appShell && !appShell.classList.contains('hidden')){
      showPaywall();
    }
  }catch(e){
    console.error('Failed to check subscription status:', e);
  }
}

function updateSubscriptionUI(){
  if(!currentUser || !currentUser.subscription) return;
  const sub = currentUser.subscription;

  // Update panel section
  const panelStatus = document.getElementById('subscriptionPanelStatus');
  if(panelStatus){
    if(sub.status === 'trial'){
      const daysLeft = sub.days_left || 0;
      panelStatus.textContent = `Trial: ${daysLeft} days left`;
    } else if(sub.status === 'beta'){
      const daysLeft = sub.days_left || 0;
      panelStatus.textContent = `Beta access: ${daysLeft} day${daysLeft === 1 ? '' : 's'} left`;
    } else if(sub.status === 'active'){
      panelStatus.textContent = 'Active: $9.99/month';
    } else if(sub.status === 'canceled'){
      panelStatus.textContent = 'Canceled';
    }
  }

  // Update subscription page if visible
  const statusCard = document.getElementById('subscriptionStatusCard');
  if(statusCard && !statusCard.classList.contains('hidden')){
    renderSubscriptionPage();
  }
}

function renderSubscriptionPage(){
  if(!currentUser || !currentUser.subscription) return;
  const sub = currentUser.subscription;

  // Status badge
  const badgeEl = document.getElementById('subBadge');
  const badgeText = document.getElementById('subBadgeText');
  if(badgeEl && badgeText){
    if(sub.status === 'trial'){
      badgeText.textContent = 'Trial';
      badgeEl.style.backgroundColor = '#def5b0';
    } else if(sub.status === 'beta'){
      badgeText.textContent = 'Beta';
      badgeEl.style.backgroundColor = '#ddd6fe';
    } else if(sub.status === 'active'){
      badgeText.textContent = 'Active';
      badgeEl.style.backgroundColor = '#d1fae5';
    } else if(sub.status === 'canceled'){
      badgeText.textContent = 'Canceled';
      badgeEl.style.backgroundColor = '#fee2e2';
    }
  }

  // Plan name
  const planName = document.getElementById('subPlanName');
  if(planName){
    const PLAN_NAMES = { trial: 'Free Trial', beta: 'Beta Access', active: 'Pro Plan' };
    planName.textContent = PLAN_NAMES[sub.status] || 'No Active Plan';
  }

  // Trial countdown
  const trialCountdown = document.getElementById('trialCountdown');
  const activeSubInfo = document.getElementById('activeSubInfo');
  if(sub.status === 'trial' || sub.status === 'beta'){
    const daysLeft = sub.days_left || 0;
    const isBeta = sub.status === 'beta';
    const daysText = document.getElementById('trialCountdownText');
    const endDate = document.getElementById('trialEndDate');
    const endLabel = document.getElementById('trialEndLabel');
    const unit = daysLeft === 1 ? 'day' : 'days';
    if(daysText) daysText.textContent = `${daysLeft} ${unit} left ${isBeta ? 'in beta access' : 'in trial'}`;
    if(endLabel) endLabel.textContent = isBeta ? 'Your beta access ends' : 'Your trial ends';
    // A beta grant runs on subscription_end_at; a trial runs on trial_ends_at.
    const endIso = isBeta ? sub.subscription_end_at : sub.trial_ends_at;
    if(endDate && endIso){
      const date = new Date(endIso);
      endDate.textContent = date.toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric'});
    }
    if(trialCountdown) trialCountdown.classList.remove('hidden');
    if(activeSubInfo) activeSubInfo.classList.add('hidden');
  } else {
    if(trialCountdown) trialCountdown.classList.add('hidden');
    // 'canceled' reuses this panel: cancelling takes effect at period end, so there is
    // still a real date to show, it just means "access ends" rather than "renews".
    const renewLabel = document.getElementById('subRenewLabel');
    const renewDate = document.getElementById('subRenewDate');
    const endIso = sub.subscription_end_at;
    const heading = document.getElementById('subActiveHeading');
    if(activeSubInfo && (sub.status === 'active' || (sub.status === 'canceled' && endIso))){
      activeSubInfo.classList.remove('hidden');
      if(heading) heading.textContent = sub.status === 'active' ? '✓ Subscription Active' : 'Subscription canceled';
      if(renewLabel) renewLabel.textContent = sub.status === 'active' ? 'Renews' : 'Access ends';
      if(renewDate){
        renewDate.textContent = endIso
          ? new Date(endIso).toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'})
          : '—';
      }
    }else if(activeSubInfo){
      activeSubInfo.classList.add('hidden');
    }
  }

  // Cancel button visibility
  const cancelBtn = document.getElementById('cancelSubBtn');
  if(cancelBtn){
    if(sub.status === 'active'){
      cancelBtn.classList.remove('hidden');
    } else {
      cancelBtn.classList.add('hidden');
    }
  }

  // Trial badge in pricing table
  const trialBadge = document.getElementById('trialBadge');
  if(trialBadge){
    if(sub.status === 'trial' || sub.status === 'beta'){
      trialBadge.classList.remove('hidden');
    } else {
      trialBadge.classList.add('hidden');
    }
  }
}

// promoInputId lets the paywall screen reuse this with its own promo field; the
// subscription page passes nothing and gets the default.
async function upgradeSubscription(promoInputId){
  if(!currentUser) return;
  try{
    // Get Stripe checkout URL
    const successUrl = window.location.origin + '?payment=success';
    const cancelUrl = window.location.origin + '?payment=canceled';

    const res = await fetch('/api/subscription/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        userid: currentUser.userid,
        email: currentUser.email,
        success_url: successUrl,
        cancel_url: cancelUrl,
        promo_code: document.getElementById(promoInputId || 'promoCodeInput')?.value?.trim() || ''
      })
    });

    if(!res.ok){
      const err = await res.json();
      alert('Error: ' + (err.error || 'Could not create checkout session'));
      return;
    }

    const data = await res.json();
    if(data.checkout_url){
      window.location.href = data.checkout_url;
    }
  }catch(e){
    console.error('Upgrade failed:', e);
    alert('Could not start checkout. Please try again.');
  }
}

async function applyPromoCode(inputId, statusId){
  if(!currentUser) return;
  const input = document.getElementById(inputId || 'promoCodeInput');
  const status = document.getElementById(statusId || 'promoStatus');
  if(!input || !status) return;

  const code = input.value.trim();
  if(!code){
    status.textContent = 'Enter a promo code';
    return;
  }

  try{
    const res = await fetch('/api/subscription/validate-promo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid: currentUser.userid, promo_code: code })
    });

    if(!res.ok){
      const err = await res.json();
      status.textContent = '✗ ' + (err.error || 'Invalid code');
      status.className = 'text-xs text-rose-600 mt-2';
      return;
    }

    const data = await res.json();
    if(!data.valid) return;

    // A "checkout" code (FREEMONTH, WELCOME10) is only a discount at Stripe — there is
    // nothing to apply yet, so say so and leave it in the box for upgradeSubscription()
    // to pass along. A "grant" code (BETAUSER) is redeemed against the account right
    // now, which is a write, so it goes to a different endpoint.
    if(data.kind !== 'grant'){
      status.textContent = '✓ ' + data.description + ' — applied at checkout';
      status.className = 'text-xs text-emerald-600 mt-2';
      return;
    }

    status.textContent = 'Applying…';
    status.className = 'text-xs text-slate-500 mt-2';
    const redeem = await fetch('/api/subscription/redeem-promo', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid: currentUser.userid, promo_code: code })
    });
    const applied = await redeem.json().catch(() => ({}));
    if(!redeem.ok){
      status.textContent = '✗ ' + (applied.error || 'Could not apply code');
      status.className = 'text-xs text-rose-600 mt-2';
      return;
    }

    status.textContent = '✓ ' + (applied.description || 'Code applied');
    status.className = 'text-xs text-emerald-600 mt-2';
    input.value = '';
    if(applied.subscription){
      currentUser.subscription = applied.subscription;
      updateSubscriptionUI();
      renderSubscriptionPage();
    }
    // Redeeming from the paywall is the whole point of this code: if it bought them
    // access back, let them straight into the app rather than leaving them staring at
    // a lock screen that no longer applies.
    const locked = document.getElementById('page-locked');
    if(hasSubscriptionAccess() && locked && !locked.classList.contains('hidden')){
      await showApp();
    }
  }catch(e){
    status.textContent = 'Could not validate code';
    status.className = 'text-xs text-rose-600 mt-2';
  }
}

async function cancelSubscription(){
  if(!currentUser || !confirm('Are you sure you want to cancel? You\'ll lose access when your current billing period ends.')) return;

  try{
    const res = await fetch('/api/subscription/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ userid: currentUser.userid })
    });

    if(!res.ok){
      const err = await res.json();
      alert('Error: ' + (err.error || 'Could not cancel'));
      return;
    }

    const result = await res.json().catch(() => ({}));
    // The backend cancels at period end, so name the date access actually stops rather
    // than the vague "end of your billing period" — it comes back on the response.
    const endsAt = result.subscription_end_at
      ? new Date(result.subscription_end_at).toLocaleDateString('en-US', {month: 'long', day: 'numeric', year: 'numeric'})
      : null;
    alert(endsAt
      ? `Subscription canceled. You'll keep full access until ${endsAt}.`
      : "Subscription canceled. You'll keep access until the end of the period you've already paid for.");
    await checkSubscriptionStatus();
  }catch(e){
    console.error('Cancel failed:', e);
    alert('Could not cancel subscription. Please try again.');
  }
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
    source: 'local',
    dbTypes: ['Conference'],
    // Only a handful of Conference-typed rows exist in the catalog — always hard-filter
    // to just those rather than falling back to the full database (see preFilter's
    // strict param), since "closest keyword match among 1200 summer programs" is a
    // worse result than "the 2 real conference venues we actually have".
    strictType: true,
    heading: 'Describe your research',
    sub: 'Tell us what your research is about, the methods or approach you used, and what stage it\'s at (early idea, in progress, or a finished paper ready to submit).',
    label: 'Describe your research',
    placeholder: 'e.g. My research investigates whether large language models encode Hindi grammatical case roles (kāraka) independently of surface case marking. I use linear probing and LEACE causal concept erasure on mBERT, HindBERT, and MuRIL...'
  },
  journal: {
    name: 'Journal Venue',
    desc: 'Academic and student journals to publish a paper in',
    source: 'local',
    dbTypes: ['Journal'],
    strictType: true,
    heading: 'Describe your research',
    sub: 'Tell us what your research is about, the methods or approach you used, and what stage it\'s at (early idea, in progress, or a finished paper ready to submit).',
    label: 'Describe your research',
    placeholder: 'e.g. My research develops a grapheme-to-phoneme system for three endangered Finnic languages — Karelian, Livonian, and Ingrian — comparing rule-based and neural approaches...'
  },
  'research-competition': {
    name: 'Research or Project Competition',
    desc: 'Science fairs, app challenges, and project-based contests',
    source: 'local',
    dbTypes: ['Research'],
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
        <div style="background-color:#eef0fb;border-radius:16px;border:none;box-shadow:none;padding:16px;text-align:left;opacity:0.6">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">
            <span class="font-heading font-bold" style="color:#8a93a6">${c.name}</span>
            <span style="background-color:#f4b400;color:#92400e;font-weight:700;font-size:10px;text-transform:uppercase;padding:2px 8px;border-radius:999px">Soon</span>
          </div>
          <p class="text-xs mt-1" style="color:#4A6685;font-size:13px">${c.desc}</p>
        </div>
      `;
    }
    return `
      <button style="background-color:#eef0fb;border-radius:16px;border:none;box-shadow:none;padding:16px;text-align:left;width:100%;transition:background-color 0.15s ease;cursor:pointer" onmouseover="this.style.backgroundColor='#dfe4f7'" onmouseout="this.style.backgroundColor='#eef0fb'" onclick="selectKind('${key}')">
        <span class="block font-heading font-bold" style="color:#1a2540">${c.name}</span>
        <span class="block text-xs mt-1" style="color:#4A6685;font-size:13px">${c.desc}</span>
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

let currentStage = 0; // Track which stage the Finder is on
// Where stage-2's back-link should return to: 1 = manual "describe your project" flow
// (stage-1), 0 = arrived via Fresh Finds' profile-based auto-match (back to stage-0).
// Set right before each goStage(2) call — see runSearch() and runFreshFindsAutoSearch().
let resultsBackTarget = 1;
function goStage(n){
  currentStage = n;
  document.querySelectorAll('.stage').forEach(s => s.classList.remove('active'));
  document.getElementById('stage-' + n).classList.add('active');
  if(n === 0) {
    renderSuggestEntryCard();
  }
  if(n === 2) updateResultsBackLink();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}
// Swaps stage-2's back-link based on resultsBackTarget. The "deepen your story" banner
// only makes sense coming from the profile-based Fresh Finds flow (manual describe-your-
// project results have no profile to deepen), and now carries its own "or browse
// opportunities" link — so the standalone browse link only needs to show for the manual
// flow, where there's no banner to carry it.
function updateResultsBackLink(){
  const deepenBanner = document.getElementById('resultsDeepen StoryBanner');
  const browseWrap = document.getElementById('resultsBrowseLinkWrap');
  const onProfileFlow = resultsBackTarget === 0;
  if(deepenBanner) deepenBanner.classList.toggle('hidden', !onProfileFlow);
  if(browseWrap) browseWrap.classList.toggle('hidden', onProfileFlow);
}
// "Click here to browse opportunities" from the results view (stage-2) — always visible
// once results are showing, as a way back to the kind picker regardless of which flow
// (profile-based Fresh Finds or manual describe-your-project) produced these results.
function browseFromResults(){
  goStage(0);
  const panel = document.getElementById('browsePanel');
  if(panel && panel.classList.contains('hidden')) toggleBrowsePanel();
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
        <button class="border-2 border-slate-900 w-full text-left p-4 rounded-xl hover:bg-slate-50 quiz-option" onclick="selectKind('${o.kind}')">
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
  const haystack = (opp.name + ' ' + opp.org + ' ' + opp.summary + ' ' + (opp.subject_tags || []).join(' ')).toLowerCase();
  let score = 0;
  tokens.forEach(t => {
    if(STOPWORDS.has(t) || t.length < 3) return;
    if(haystack.includes(t)) score += 1;
  });
  return score;
}

// ---------- Grade-level eligibility ----------
// Maps a grade-level mention — from the Finder's explicit "Grade level" dropdown
// (index.html) or free text in a student's profile/description — to a single US grade
// number (6-12), the same scale as the DB's grade_min/grade_max columns. "Middle School"
// resolves to 8 (its upper end) since grade_min/grade_max never goes below 6.
const GRADE_WORD_TO_NUM = { freshman: 9, sophomore: 10, junior: 11, senior: 12 };
function parseGradeFromText(text){
  if(!text) return null;
  const lower = text.toLowerCase();
  // "9th grade", "grade 9", "9th-grade", etc.
  let m = lower.match(/\b(6|7|8|9|10|11|12)(?:st|nd|rd|th)?\s*[- ]?\s*grade\b/) || lower.match(/\bgrade\s*[- ]?\s*(6|7|8|9|10|11|12)\b/);
  if(m) return parseInt(m[1], 10);
  // "freshman"/"sophomore"/"junior"/"senior", optionally "rising" (about to be that grade)
  m = lower.match(/\b(?:rising\s+)?(freshman|sophomore|junior|senior)\b/);
  if(m) return GRADE_WORD_TO_NUM[m[1]];
  if(/\bmiddle school\b/.test(lower)) return 8;
  return null;
}
// Explicit dropdown values share the same phrasing as free text, so route through the
// same parser rather than maintaining a second mapping.
function parseGradeLevel(label){
  return parseGradeFromText(label);
}
// True if the opportunity's grade_min/grade_max range (if set) includes studentGrade.
// Most rows have no grade bounds at all (only the opportunity-finder-sourced subset does)
// — those are eligible for everyone. If the student's grade is unknown, nothing is filtered.
function isGradeEligible(opp, studentGrade){
  if(studentGrade == null) return true;
  if(opp.grade_min == null && opp.grade_max == null) return true;
  if(opp.grade_min != null && studentGrade < opp.grade_min) return false;
  if(opp.grade_max != null && studentGrade > opp.grade_max) return false;
  return true;
}

function preFilter(description, subjectHints, typeFilter, strict, studentGrade){
  const tokens = [...new Set(tokenize(description).filter(t => !STOPWORDS.has(t) && t.length >= 3))];
  const subjSet = new Set((subjectHints || []).map(s => s.toLowerCase()));
  const typeSet = typeFilter && typeFilter.length ? new Set(typeFilter) : null;

  let base = OPPORTUNITIES;
  if(typeSet){
    const byType = OPPORTUNITIES.filter(o => typeSet.has(o.type));
    // Only hard-filter by type if it leaves a reasonable pool — otherwise the
    // Type field for this kind is too sparse to be a useful constraint. `strict`
    // (set by kinds like Conference/Journal Venue, whose Type is rare but exact)
    // skips this size gate: an always-tiny, always-correct pool beats falling back
    // to keyword-matching the entire 1200+ row catalog.
    if(byType.length >= 15 || (strict && byType.length > 0)){ base = byType; }
  }
  if(studentGrade != null){
    // Hard filter, no size-gate fallback: nearly all rows have null grade bounds
    // (eligible for everyone), so this only ever excludes the subset with an actual
    // grade_min/grade_max range that doesn't cover the student — it can't collapse the pool.
    base = base.filter(o => isGradeEligible(o, studentGrade));
  }

  const scored = base.map(opp => {
    let score = keywordScore(tokens, opp);
    if((opp.subject_tags || []).some(t => subjSet.has((t || '').toLowerCase()))) score += 3;
    return { opp, score };
  });
  scored.sort((a, b) => b.score - a.score);
  const withScore = scored.filter(s => s.score > 0);
  // Capped at 100 (was 180) — rankCandidates only ever keeps the best 10-12 anyway,
  // and a smaller candidate payload means Claude has less to read before it can
  // respond, which was a real chunk of the wait on a search.
  const pool = (withScore.length >= 60 ? withScore : scored).slice(0, 100).map(s => s.opp);
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

// ---------- Gemini API helpers ----------
// Sends a plain, backend-agnostic {system, userContent, useWebSearch} body — server.py's
// /api/messages owns the actual Gemini request shape (model pin, thinking-budget config,
// forced-search nudge) via gemini_common.call_gemini(), so this client code doesn't need
// to know or change if that wire format changes. Response is normalized server-side back
// into a {content:[{type:"text",text:...}]} envelope (both live and mock modes), so the
// parsing below stays the same either way.
async function callGemini(system, userContent, useWebSearch){
  const body = { system, userContent, useWebSearch: !!useWebSearch, userid: currentUser?.userid || null };
  const res = await fetch("/api/messages", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if(res.status === 402){ handleSubscriptionLapsed(); throw new Error("Subscription required"); }
  if(!res.ok){ throw new Error(`API error ${res.status}`); }
  const data = await res.json();
  const textBlocks = (data.content || []).filter(b => b.type === "text").map(b => b.text).join("\n");
  const clean = textBlocks.replace(/```json|```/g, "").trim();
  if(!clean){ throw new Error("Empty response from API"); }
  return clean;
}

// ---------- Claude API helper (profile chat only) ----------
// profileChatNextQuestion/profileChatStarterQuestionsFromAI deliberately stayed on Claude
// (Haiku 4.5) rather than moving to Gemini with the rest of the app — same client body
// shape as callGemini ({system, userContent, useWebSearch}), just posted to a separate
// endpoint so server.py can translate it into Anthropic's request format internally.
async function callClaude(system, userContent, useWebSearch){
  const body = { system, userContent, useWebSearch: !!useWebSearch, userid: currentUser?.userid || null };
  const res = await fetch("/api/messages-claude", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if(res.status === 402){ handleSubscriptionLapsed(); throw new Error("Subscription required"); }
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

// Calls Gemini/Claude and extracts JSON from the response, retrying the whole request
// once if extractJSON fails to parse it. Occasionally the model emits a structurally
// malformed value (e.g. an unescaped quote inside a "reason" string breaking a JSON
// string mid-array) that extractJSON's truncation-repair can't fix since the damage
// isn't at the end — this is a one-off formatting glitch, not something that reliably
// repeats on a fresh request with the same prompt, so a single retry converts most of
// these into a normal successful response instead of a hard user-facing error.
async function callGeminiJSON(system, userContent, useWebSearch){
  try{
    return extractJSON(await callGemini(system, userContent, useWebSearch));
  }catch(err){
    console.warn('JSON parse failed, retrying once:', err.message);
    return extractJSON(await callGemini(system, userContent, useWebSearch));
  }
}
async function callClaudeJSON(system, userContent, useWebSearch){
  try{
    return extractJSON(await callClaude(system, userContent, useWebSearch));
  }catch(err){
    console.warn('JSON parse failed, retrying once:', err.message);
    return extractJSON(await callClaude(system, userContent, useWebSearch));
  }
}

const VALID_SUBJECTS = ['Mixed','STEM','Medicine','Humanities','Art','Business','Engineering','Computer Science','Mathematics','Biology','Physics','Astronomy','Chemistry','Leadership','Law','Logic','Education'];

async function inferSubjects(description){
  const system = `You infer which subject categories from a fixed list best match a student's passion-project description. Valid categories (use these exact strings): ${VALID_SUBJECTS.join(', ')}. Respond with ONLY a raw JSON array of 2-5 of the most relevant category strings, no markdown, no preamble. Example: ["Computer Science","STEM","Mathematics"]`;
  const arr = await callGeminiJSON(system, description, false);
  return Array.isArray(arr) ? arr.filter(s => VALID_SUBJECTS.includes(s)) : [];
}

// The five "basics" tiles on My Vibe are read out of the profile text rather than
// collected as their own form — the student only ever types prose, so anything they
// never mentioned stays blank rather than showing a made-up default.
const PROFILE_BASICS_FIELDS = [
  { key: 'grade', label: 'Grade level' },
  { key: 'state', label: 'Home state' },
  { key: 'gender', label: 'Gender' }
];

async function extractProfileBasics(text){
  if(!text || !text.trim()) return {};
  const system = `You read a high school student's self-description and pull out a small set of specific profile facts, ONLY if the student actually stated or clearly implied them. Respond with ONLY a raw JSON object (no markdown, no preamble) with exactly these keys: "grade" (their school year, e.g. "11th grade"), "state" (US state or region they live in, spelled out), "gender". Set a key to null if the student did not say it — never guess, never infer from stereotypes, and never fill a value in just to avoid a null.`;
  const obj = await callGeminiJSON(system, text, false);
  if(!obj || typeof obj !== 'object' || Array.isArray(obj)) return {};
  const out = {};
  PROFILE_BASICS_FIELDS.forEach(({ key }) => {
    const v = obj[key];
    out[key] = (typeof v === 'string' && v.trim() && !/^(null|n\/?a|unknown|unspecified)$/i.test(v.trim())) ? v.trim() : null;
  });
  return out;
}

async function rankCandidates(description, candidates, prefs, requireAll){
  const compact = candidates.map(c => ({ id: c.id, name: c.name, org: c.org, summary: c.summary, subject_tags: c.subject_tags, type: c.type, price: c.price, location: c.location, season: c.season }));
  // requireAll (set for strict-type kinds like Conference/Journal Venue, where the
  // candidate list IS the entire real catalog for that type, not a large pre-filtered
  // pool) — tell Claude to rank every candidate instead of omitting weak fits. With
  // only 2-3 real venues total, "omit anything that isn't a great fit" too easily
  // zeroes out the whole result set and surfaces a misleading "no matches" error even
  // though real venues exist; better to show them all, honestly tiered, and let the
  // student judge.
  const selectionRule = requireAll
    ? "Rank and return EVERY candidate given — this is an exhaustive list of the only known real options of this type, so do not omit any even if the fit is loose."
    : "Select ONLY the opportunities that would genuinely help them grow this specific project, build relevant skills, get recognition for it, or connect with the right community — not just anything thematically adjacent. Leave out weak or generic fits entirely; every opportunity you return must be a genuinely good match. Rank the best 10-12 matches only.";
  const system = `You are Wingman, helping a student find the best-fit extracurricular opportunities (programs, internships, competitions, research positions) for their specific passion project, from a candidate list. Read their project description and preferences carefully. ${selectionRule} For each, write a short specific reason (under 15 words) that names or clearly paraphrases an actual detail from THEIR description/preferences below (a subject, skill, project, goal, or interest they stated) — never write a generic reason that could apply to any student interested in this general field, and never invent details they didn't mention. Write the reason as Wingman speaking directly TO the student in second person ("you"/"your") — e.g. "Great fit for your robotics build" not "Good fit for the student's robotics project" or "Good fit for their robotics project." Assign a tier: 'strong' (excellent, highly specific fit) or 'look' (solid, worth a look). Respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{"id":"...","reason":"...","tier":"strong|look"}]. Stay well within a 1000-token response — 10-12 items is a hard cap.`;
  const prefsText = prefs ? `\n\nStudent preferences: ${prefs}` : '';
  const userContent = `Student's passion project:\n${description}${prefsText}\n\nCandidate opportunities (JSON):\n${JSON.stringify(compact)}\n\nSelect and rank the best matches per the schema.`;
  const arr = await callGeminiJSON(system, userContent, false);
  return Array.isArray(arr) ? arr : [];
}

// ---------- Web-search path (unused by any kind currently) ----------
// Conference Venue / Journal Venue used this until the catalog gained real
// Conference/Journal-typed rows and moved to the local-database path (see
// KIND_CONFIG). Left in place as a fallback for any future kind whose type
// isn't well represented locally.
async function findVenuesViaWeb(description, cfg, prefsText){
  const today = todayLabel();
  const system = `You help a student researcher find real, current ${cfg.venueKind} that fit their specific research. Today's date is ${today}. Use web_search to find and verify actual venues — don't rely only on memorized knowledge, since deadlines and calls-for-papers change. Prefer venues realistically accessible to a high-school or early-career researcher (student research workshops, high-school-friendly journals, open/inclusive workshops), but you can include 1-2 more ambitious or competitive options too.

Screen out discontinued venues: if you find explicit signals a venue is discontinued, paused, or no longer accepting submissions (e.g. "no longer accepting submissions," a dead/404 page, an org site with no trace of it continuing), DO NOT include it in your results at all — skip it and find a real alternative instead.

Date handling: if a venue's listed submission deadline has already passed but it runs on a regular annual/recurring cycle, estimate next cycle's deadline from the prior cycle's timing and set was_estimated to true. Only include a next_deadline_iso when you found or can reasonably estimate one; use null if genuinely unknown. Never invent a date with no basis.

Only include opportunities that are a genuinely good fit — omit weak or generic matches entirely. For each, the "reason" must name or clearly paraphrase an actual detail from the student's research description/preferences below (a topic, method, skill, or goal they stated) — never a generic reason that could apply to any student in this broad field. Write the reason as Wingman speaking directly TO the student in second person ("you"/"your") — e.g. "Great fit for your climate modeling work" not "Good fit for the student's climate modeling project." For each of the best 6-8 matches, respond with ONLY a raw JSON array, no markdown, no preamble, no text after the array, matching: [{\"name\":\"official venue name, include year if known\",\"url\":\"the venue's official URL\",\"org\":\"organizing body, short\",\"summary\":\"under 18 words on scope/format\",\"reason\":\"under 15 words on why it fits THIS research specifically, addressed directly to the student\",\"tier\":\"strong|look\",\"next_deadline_iso\":\"YYYY-MM-DD or null\",\"was_estimated\":true or false}]. Stay well within a 1000-token response — 6-8 items is a hard cap, keep every field short.`;
  const prefsPart = prefsText ? `\nStudent preferences: ${prefsText}` : '';
  const userContent = `Research description:\n${description}${prefsPart}\n\nSearch the web and find the best matching real, current ${cfg.name.toLowerCase()} options.`;
  const arr = await callGeminiJSON(system, userContent, true);
  if(!Array.isArray(arr)) return [];
  return arr.map(item => {
    const opp = {
      id: slugify(item.name || item.url || 'venue'),
      name: item.name || 'Untitled venue',
      org: item.org || '',
      summary: item.summary || '',
      url: item.url || '#',
      subject_tags: [],
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

let studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0, filterValues: null, filterTags: null };

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
      // Values derived from the profile text (see getProfileDerived) — a profile saved
      // before these were introduced simply has none, and the next reader computes them.
      Object.keys(PROFILE_DERIVED_SLOTS).forEach(slot => {
        studentProfile[slot] = (parsed[slot] && typeof parsed[slot] === 'object') ? parsed[slot] : null;
      });
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
// Threshold below which a profile is treated as "insufficient" for auto-matching on the
// Fresh Finds landing (mirrors the "aim for at least 20 words" guidance shown on the
// manual describe-your-project textarea, so the bar is the same one students already see).
const PROFILE_SUFFICIENT_LENGTH = 20;
// Count words in a profile string (split by whitespace)
function countProfileWords(text){
  if(!text) return 0;
  return text.trim().split(/\s+/).filter(w => w.length > 0).length;
}

// ---------- Values derived from the profile text (subjects, grade, filter tags) ----------
// Three things are derived from the profile text and nothing else: the subject categories
// preFilter() wants (an AI call — inferSubjects), the student's grade (a local regex
// parse), and the enriched "Your Profile" tags on the results filter bar (two AI calls —
// buildProfileFilterTags). Because they depend only on the text, they are computed once
// when the profile is updated and stored on studentProfile alongside it. Fresh Finds used
// to recompute all of it on every single load — a subject-inference call before the
// search, then 1 + N tag calls in front of the results — to get the same answers back from
// an unchanged profile. Anything below this many words of change is treated as a touch-up
// (rewording, a clarifying sentence) that wouldn't move any of them, so what's stored stands.
const PROFILE_FILTER_REFRESH_WORDS = 10;

// They are stored as two independent slots, `filterValues` and `filterTags`, rather than
// one record, because their consumers can't wait on each other: a search needs subjects
// before it can pre-filter, and tag building is two calls that would otherwise sit in
// front of it for nothing (the tags only feed a filter dropdown further down the page).
// Each slot carries its own copy of the text it was computed from, so one can be stale,
// missing, or mid-refresh without saying anything about the other.
const PROFILE_DERIVED_SLOTS = {
  // Note the freshness check is `computedAt`, not array length: an empty result is a
  // legitimate answer (inferSubjects drops anything outside VALID_SUBJECTS; a thin profile
  // may yield no tags), and treating it as "not computed yet" would re-pay for that same
  // empty answer on every single load.
  filterValues: {
    key: 'subjects',
    async compute(text){
      return { subjects: await inferSubjects(text), grade: parseGradeFromText(text) };
    }
  },
  filterTags: {
    key: 'enrichedTags',
    async compute(text){
      return { enrichedTags: await buildProfileFilterTags(text) };
    }
  },
  // The My Vibe basics tiles. `fields` is an object, not an array, so its freshness
  // check can't use the array-shaped `key` path the other two slots take.
  basics: {
    key: 'fields',
    isFilled: rec => !!(rec && rec.fields && typeof rec.fields === 'object'),
    async compute(text){
      return { fields: await extractProfileBasics(text) };
    }
  }
};
// slot -> { text, promise } for a computation already running, so a background refresh
// started by mergeIntoProfile and a search landing mid-flight share one call.
const profileDerivedInFlight = {};

function profileDerivedIsFresh(slot, rec, text){
  const cfg = PROFILE_DERIVED_SLOTS[slot];
  const filled = cfg.isFilled || (r => Array.isArray(r[cfg.key]));
  if(!rec || !rec.computedAt || !filled(rec)) return false;
  if(rec.profile === text) return true;
  return Math.abs(countProfileWords(text) - (rec.wordCount || 0)) < PROFILE_FILTER_REFRESH_WORDS;
}

// Returns the stored values when the profile hasn't meaningfully changed since they were
// computed, otherwise computes and persists them now — so a profile that predates this
// cache, or one edited while storage was unavailable, still works; it just pays once.
async function getProfileDerived(slot){
  const text = studentProfile.synthesized || '';
  if(profileDerivedIsFresh(slot, studentProfile[slot], text)) return studentProfile[slot];
  const flight = profileDerivedInFlight[slot];
  if(flight && flight.text === text) return flight.promise;
  const promise = (async () => {
    try{
      const rec = Object.assign(await PROFILE_DERIVED_SLOTS[slot].compute(text), {
        profile: text,
        wordCount: countProfileWords(text),
        computedAt: new Date().toISOString()
      });
      // The profile can be edited while this is in flight; don't overwrite values for text
      // that's already been superseded — the next call recomputes against the new text.
      if((studentProfile.synthesized || '') === text){
        studentProfile[slot] = rec;
        await saveProfile();
      }
      return rec;
    }finally{
      if(profileDerivedInFlight[slot] && profileDerivedInFlight[slot].text === text) delete profileDerivedInFlight[slot];
    }
  })();
  profileDerivedInFlight[slot] = { text, promise };
  return promise;
}

// The one way search flows should read subjects + grade.
function getProfileFilterValues(){ return getProfileDerived('filterValues'); }
// ...and the one way the results filter bar should read its tags.
function getProfileFilterTags(){ return getProfileDerived('filterTags'); }

// Synchronous read of the stored tags, for callers that must not block (renderResults
// paints the filter bar from this before deciding whether anything is missing). Returns
// null — distinct from [] — when nothing has been computed for the current text yet.
function cachedProfileFilterTags(){
  const rec = studentProfile.filterTags;
  if(!profileDerivedIsFresh('filterTags', rec, studentProfile.synthesized || '')) return null;
  return rec.enrichedTags;
}

// Fire-and-forget refresh after a profile edit (see mergeIntoProfile), so neither a search
// nor a results render has to pay for these. Both slots go at once — they don't block each
// other here. A failure is not user-facing: the next reader recomputes, or does without
// (no subject hints for the pre-filter, no tag facet on the bar).
function refreshProfileFilterValues(){
  if(!studentProfile.synthesized) return;
  Object.keys(PROFILE_DERIVED_SLOTS).forEach(slot => {
    getProfileDerived(slot).catch(err => console.warn(`Profile ${slot} refresh failed:`, err.message));
  });
}
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
      <div class="bg-amber-50 border-2 border-amber-400 rounded-2xl p-3 mb-5 flex flex-wrap items-center justify-between gap-2">
        <p class="text-xs font-bold text-amber-900">It's been ${days} days since you updated your profile — refresh it for the best matches.</p>
        <button class="pop-btn bg-white text-slate-900 font-bold px-3 py-1.5 rounded-xl text-xs shrink-0" onclick="focusProfileChat()">↓ Update via chat</button>
      </div>
    ` : '';
  }
  const clearBtn = document.getElementById('profileClearBtn');
  if(clearBtn) clearBtn.classList.toggle('hidden', !hasProfile);
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
// Navigate to Fresh Finds and open the browse-by-opportunity-type panel.
function goToBrowseOpportunities(){
  showPage('wizard');
  setTimeout(() => {
    goStage(0);
    const panel = document.getElementById('browsePanel');
    if(panel && panel.classList.contains('hidden')) toggleBrowsePanel();
  }, 150);
}
// The single entry point for "deepen my story" everywhere in the app (the Profile
// page's own buttons, the stale-profile banner, the Home teaser, the Finder's
// pre-search nudge). The drawer starts closed and its starter questions are never
// fetched until the student explicitly asks to go deeper — this is the only place that
// opens it and calls initProfileChat() (which is what actually triggers the
// profileChatStarterQuestionsFromAI API call), so simply visiting the Profile tab never
// spends an API call on its own.
function openStoryDrawer(){
  const drawer = document.getElementById('storyDrawer');
  const overlay = document.getElementById('storyDrawerOverlay');
  if(!drawer) return;
  overlay.classList.remove('hidden');
  drawer.classList.add('open');
  drawer.setAttribute('aria-hidden', 'false');
  initProfileChat();
  const input = document.getElementById('profileChatInput');
  if(input) setTimeout(() => input.focus(), 300);
}

// Closing IS the save: clicking the backdrop, the ✕, or hitting Escape all fold whatever
// the student shared in this session into the profile (see finishProfileChatSession),
// which then flashes the newly-added sentences. The drawer shuts immediately rather than
// waiting on that call — the merge takes a couple of API round-trips, and holding a
// dismissed drawer open for them reads as broken.
function closeStoryDrawer(){
  const drawer = document.getElementById('storyDrawer');
  const overlay = document.getElementById('storyDrawerOverlay');
  if(!drawer || !drawer.classList.contains('open')) return;
  overlay.classList.add('hidden');
  drawer.classList.remove('open');
  drawer.setAttribute('aria-hidden', 'true');
  if(profileChatHistory.some(m => m.role === 'user')) finishProfileChatSession();
}

// Kept as the app-wide alias so existing "deepen your story" call sites keep working.
function focusProfileChat(){
  openStoryDrawer();
}

function openImportModal(){
  const modal = document.getElementById('importModal');
  if(modal) modal.classList.remove('hidden');
}
function closeImportModal(){
  const modal = document.getElementById('importModal');
  if(modal) modal.classList.add('hidden');
}

document.addEventListener('keydown', (e) => {
  if(e.key !== 'Escape') return;
  const modal = document.getElementById('importModal');
  if(modal && !modal.classList.contains('hidden')){ closeImportModal(); return; }
  closeStoryDrawer();
});

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
  studentProfile = { synthesized: '', updatedAt: null, chatRounds: 0, filterValues: null, filterTags: null };
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
  // Only refresh the chat (and re-spend an API call on fresh starters) if the drawer is
  // already open — clearing the profile shouldn't be what opens it.
  const drawer = document.getElementById('storyDrawer');
  if(drawer && drawer.classList.contains('open')) initProfileChat();
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
  const before = studentProfile.synthesized;
  try{
    studentProfile.synthesized = await synthesizeProfile(studentProfile.synthesized, text.trim());
  }catch(e){
    console.error('Profile synthesis failed, appending instead:', e);
    studentProfile.synthesized = studentProfile.synthesized ? studentProfile.synthesized + ' ' + text.trim() : text.trim();
  }
  studentProfile.updatedAt = new Date().toISOString();
  profileBasicsUnavailable = false;
  // Swap the optimistic tile for the real thing in one render, rather than clearing it a
  // tick earlier (the card would flicker back to its old state) or a tick later (the
  // student's words would briefly appear twice, once raw and once merged).
  profilePendingText = null;
  flagNewProfileText(before, studentProfile.synthesized);
  await saveProfile();
  // The profile text just changed, so its derived search filter values may be stale.
  // Recompute them here rather than on the next Fresh Finds load — that's the whole
  // point of caching them (see getProfileFilterValues). Deliberately not awaited: the
  // student is looking at their updated profile, not waiting on a search.
  refreshProfileFilterValues();
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

// Predetermined starter questions — used when profile is empty/insufficient (no API cost).
// Diverse questions covering music, sports, hobbies, personality, jobs, leadership, etc.
// Rotates through different sets per user so variety is maintained across users.
const PREDETERMINED_STARTER_QUESTIONS = [
  "If your extracurriculars had a theme song, what would it be — and why does that fit you?",
  "What's something you're weirdly good at that has nothing to do with school?",
  "If you had one free Saturday with zero obligations, what would you actually do with it?",
  "What's a skill you're trying to get better at that's purely for fun?",
  "Tell me about the last time you got totally absorbed in something — what was it?",
  "Do you have any quirky obsessions or guilty pleasures we should know about?",
  "What was the last time you felt genuinely proud of yourself — what did you do?",
  "If you could be part of any group or team (real or imaginary), what would it be?",
  "What's something about your personality that surprises people when they get to know you?",
  "Do you create or make anything (art, music, code, video, crafts, cooking)? What appeals to you?",
  "What role do you usually play in group projects or friend groups — leader, organizer, listener, joker?",
  "Have you ever had a job or volunteer gig? What did you learn about yourself?",
  "What kind of stuff makes you lose track of time in the best way?",
  "If you could teach someone else one skill you have, what would it be?",
  "What's a topic or hobby you know way more about than most people your age?",
  "Tell me about someone who inspires you and why they do.",
  "What's something you've done that took guts or got you out of your comfort zone?",
  "Do you play sports or do any athletic stuff? Or is movement/fitness not really your thing?",
  "What's the most fun you've had in the last few months?",
  "Are you more of a solo person or do you prefer hanging with others?",
  "What would your friends say is your superpower?",
  "Have you ever been really into a cause, movement, or community (online or IRL)?",
];

let predeterminedStarterRotationIndex = 0; // Track which set of 3 we're on

// Generic fallback starters, used only if the AI call fails AND profile exists
// (or times out) — so a flaky connection never leaves the chat stuck with nothing to show.
const FALLBACK_STARTER_QUESTIONS = [
  "What's something you're weirdly good at that has nothing to do with school?",
  "If you had one free Saturday with zero obligations, what would you actually do with it?",
  "What was the last time you felt genuinely proud of yourself — what did you do?"
];

// Gets the next 3 questions from the predetermined pool, rotating through different sets.
// This ensures variety across users and sessions when profile is empty/insufficient.
function getNextPredeterminedStarterQuestions(){
  const pool = PREDETERMINED_STARTER_QUESTIONS;
  const startIdx = (predeterminedStarterRotationIndex * 3) % pool.length;
  predeterminedStarterRotationIndex = (predeterminedStarterRotationIndex + 1) % Math.ceil(pool.length / 3);

  const result = [];
  for(let i = 0; i < 3; i++){
    result.push(pool[(startIdx + i) % pool.length]);
  }
  return result;
}

// Check if profile is empty or has very minimal content (< 50 words = too thin for AI personalization).
function isProfileInsufficientForAI(){
  const synthesized = studentProfile.synthesized || '';
  const wordCount = synthesized.trim().split(/\s+/).filter(w => w.length > 0).length;
  return wordCount < 50;
}

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
  initProfileChatVoiceUI();
}

// ============================================================
// Profile builder chat — voice input/output (Web Speech API)
// ============================================================
// SpeechRecognition (dictating an answer) and speechSynthesis (the bot reading its
// question aloud) are two unrelated browser APIs with independent support — each control
// is feature-detected and hidden individually rather than gating on one combined
// "voice mode", so e.g. a browser with TTS but no STT still gets the speaker toggle.
const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition;
let voiceRecognition = null; // lazily constructed on first mic tap, then reused
let voiceListening = false;
let voiceOutputEnabled = false; // off by default — opt-in per session, resets on reload

function initProfileChatVoiceUI(){
  const micBtn = document.getElementById('profileChatMicBtn');
  const speakerBtn = document.getElementById('profileChatSpeakerBtn');
  if(micBtn) micBtn.classList.toggle('hidden', !SpeechRecognitionCtor);
  if(speakerBtn) speakerBtn.classList.toggle('hidden', !('speechSynthesis' in window));
}

// Toggles dictation on/off. Interim results stream live into the text input as the student
// talks so they can see it working (and edit before sending); the final transcript is
// auto-submitted the moment recognition naturally ends, mirroring hitting Enter on a typed
// answer — voice shouldn't need its own separate "send" step.
function toggleVoiceInput(){
  if(!SpeechRecognitionCtor) return;
  if(voiceListening){
    voiceRecognition && voiceRecognition.stop();
    return;
  }
  if(!voiceRecognition){
    voiceRecognition = new SpeechRecognitionCtor();
    voiceRecognition.lang = 'en-US';
    voiceRecognition.interimResults = true;
    voiceRecognition.maxAlternatives = 1;
    voiceRecognition.onresult = (e) => {
      const input = document.getElementById('profileChatInput');
      if(!input) return;
      let transcript = '';
      for(let i = 0; i < e.results.length; i++){ transcript += e.results[i][0].transcript; }
      input.value = transcript;
    };
    voiceRecognition.onend = () => {
      voiceListening = false;
      updateVoiceMicUI();
      const input = document.getElementById('profileChatInput');
      if(input && input.value.trim()) sendProfileChatMessage();
    };
    voiceRecognition.onerror = (e) => {
      console.error('Speech recognition error:', e.error);
      voiceListening = false;
      updateVoiceMicUI();
    };
  }
  try{
    voiceRecognition.start();
    voiceListening = true;
    updateVoiceMicUI();
  }catch(e){
    console.error('Could not start speech recognition:', e);
  }
}

function updateVoiceMicUI(){
  const micBtn = document.getElementById('profileChatMicBtn');
  if(!micBtn) return;
  micBtn.classList.toggle('mic-listening', voiceListening);
  micBtn.textContent = voiceListening ? '⏺' : '🎤';
  micBtn.title = voiceListening ? 'Stop recording' : 'Speak your answer';
}

// Mutes/unmutes the bot reading its questions aloud. Flipping it on mid-chat immediately
// reads the most recent bot question so it doesn't feel broken/inert until the next turn.
function toggleVoiceOutput(){
  voiceOutputEnabled = !voiceOutputEnabled;
  const btn = document.getElementById('profileChatSpeakerBtn');
  if(btn){
    btn.textContent = voiceOutputEnabled ? '🔊' : '🔇';
    btn.title = voiceOutputEnabled ? 'Voice replies on — click to mute' : 'Turn on spoken questions';
    btn.classList.toggle('bg-indigo-100', voiceOutputEnabled);
  }
  if(voiceOutputEnabled){
    const lastBot = [...profileChatHistory].reverse().find(m => m.role === 'bot');
    if(lastBot) speakProfileChatText(lastBot.text);
  }else if('speechSynthesis' in window){
    window.speechSynthesis.cancel();
  }
}

function speakProfileChatText(text){
  if(!voiceOutputEnabled || !('speechSynthesis' in window) || !text) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  window.speechSynthesis.speak(utter);
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
// If profile is empty/insufficient, uses predetermined questions (no API cost).
// If profile exists and is substantial, calls Claude for personalized questions.
// `regenerate` — set when the student clicked "Regenerate" on an already-loaded set of
// starters (see regenerateProfileChatStarters) — swaps in a directive that explicitly
// prioritizes breadth (new, untouched areas of their life) over depth (drilling further
// into interests the profile already covers well).
async function profileChatStarterQuestionsFromAI(regenerate){
  // Cost-optimization: use predetermined questions when profile is empty or too thin.
  // This avoids unnecessary API calls while still providing high-quality icebreakers.
  if(isProfileInsufficientForAI()){
    return getNextPredeterminedStarterQuestions();
  }

  // Profile is substantial enough for personalized AI questions.
  const breadthDirective = regenerate ? ` The student explicitly asked to regenerate these — swap in a fresh set. Prioritize BREADTH over depth: favor surfacing entirely new areas of their life the profile hasn't touched at all (academics, social life, jobs, family, random obsessions, sports, art, gaming, etc.) over drilling further into what's already well-covered. Where a question does build on something they've already mentioned, use it only as a springboard to go one layer deeper on that specific thing — but most of the three should open up completely uncovered territory rather than deepen existing ones.` : '';
  const system = `You are a friendly, upbeat chatbot helping a high schooler build a detailed personal profile for finding extracurricular opportunities (research programs, internships, competitions, summer programs). You'll be given their CURRENT PROFILE SUMMARY (may be empty). Come up with exactly THREE distinct, short, fun, wacky-but-meaningful icebreaker questions to kick off a chat session that probes for details the profile is missing or only has shallowly — think music, sports/athletics, hobbies, what they do purely for fun, leadership, part-time jobs, quirks of personality, or deeper specifics on things already mentioned.${breadthDirective} Keep each one playful and casual, like a clever friend riffing with them, not a form — but each must serve a real purpose in understanding this student for extracurricular/college-application matching. This is chat round ${studentProfile.chatRounds + 1} of them returning to this page — the higher that number, the more specific and creative the questions should get. Respond with ONLY a JSON array of exactly 3 short question strings, e.g. ["...", "...", "..."] — no markdown, no preamble, no numbering.`;
  const userContent = `CURRENT PROFILE SUMMARY:\n${studentProfile.synthesized || '(empty)'}\n\nRespond with a JSON array of exactly 3 starter questions only.`;
  const parsed = await withTimeout(callClaudeJSON(system, userContent, false), 20000, 'Timed out waiting for starter questions');
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
  speakProfileChatText(q);
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
  let botText;
  try{
    const question = await profileChatNextQuestion();
    botText = question || "What's something you're into that might surprise people?";
  }catch(e){
    console.error('Profile chat question failed:', e);
    botText = "Hmm, I couldn't think of a question just now — want to just tell me something about yourself?";
  }
  profileChatHistory.push({ role: 'bot', text: botText });
  profileChatBusy = false;
  renderProfileChatMessages();
  speakProfileChatText(botText);
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
  const raw = await callGemini(system, userContent, false);
  return raw.trim();
}

// Called when the drawer closes, which is now the only way a chat session ends. The
// transcript is cleared either way — on failure the student's answers are gone, so say so
// out loud on the page rather than only in the drawer they just dismissed. Starters are
// deliberately NOT preloaded here: that costs an API call for a panel nobody is looking at.
async function finishProfileChatSession(){
  const statusEl = document.getElementById('profileSaveStatus');
  const setStatus = (text, color) => {
    if(!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = color;
  };
  const answers = profileChatHistory.filter(m => m.role === 'user').map(m => m.text);
  if(!answers.length) return;
  // Paint the student's own answers onto the card before awaiting anything, so closing the
  // drawer updates the profile in the same frame it closes in.
  profilePendingText = answers.join(' ');
  renderProfileFit();
  setStatus('', '#8a93a6');
  try{
    const findings = await summarizeProfileChat();
    await mergeIntoProfile(findings);
    studentProfile.chatRounds += 1;
  }catch(e){
    console.error('Profile chat summarize/merge failed:', e);
    setStatus("Couldn't fold that chat into your profile — try again in a moment.", '#d64545');
    profilePendingText = null;
    renderProfileFit();
  }
  profileChatHistory = [];
  profileChatStarters = null;
  profileChatStartersLoading = false;
  await saveProfile();
  renderProfileChatMessages();
}

// ============================================================
// Resume / LinkedIn Profile Import — quickly build profile from existing documents
// ============================================================

// Tab switching for import card
function switchImportTab(tabName){
  const resumeTab = document.getElementById('resumeTab');
  const linkedinTab = document.getElementById('linkedinTab');
  const resumeContent = document.getElementById('resumeTab-content');
  const linkedinContent = document.getElementById('linkedinTab-content');

  if(tabName === 'resume'){
    resumeTab.style.borderColor = '#00b2ca';
    resumeTab.style.color = '#00b2ca';
    linkedinTab.style.borderColor = 'transparent';
    linkedinTab.style.color = '#8a93a6';
    resumeContent.classList.remove('hidden');
    linkedinContent.classList.add('hidden');
  }else{
    resumeTab.style.borderColor = 'transparent';
    resumeTab.style.color = '#8a93a6';
    linkedinTab.style.borderColor = '#00b2ca';
    linkedinTab.style.color = '#00b2ca';
    resumeContent.classList.add('hidden');
    linkedinContent.classList.remove('hidden');
  }
}

// LinkedIn extraction — text paste only (URL fetching is blocked by LinkedIn's anti-scraping measures)

// Handle resume file upload
function handleResumeUpload(input){
  const file = input.files[0];
  if(!file) return;

  const statusEl = document.getElementById('resumeUploadStatus');
  const submitBtn = document.getElementById('resumeSubmitBtn');

  // Validate file type and size
  const validTypes = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
  const maxSize = 5 * 1024 * 1024; // 5MB

  if(!validTypes.includes(file.type)){
    statusEl.textContent = '❌ Please upload a PDF or Word document';
    statusEl.style.color = '#d64545';
    submitBtn.style.display = 'none';
    return;
  }

  if(file.size > maxSize){
    statusEl.textContent = '❌ File is too large (max 5MB)';
    statusEl.style.color = '#d64545';
    submitBtn.style.display = 'none';
    return;
  }

  statusEl.textContent = `✓ ${file.name} ready to extract`;
  statusEl.style.color = '#4c6a1a';
  submitBtn.style.display = 'block';
  // Store file for submission
  window.resumeFileToUpload = file;
}

// Submit resume extraction
async function submitResumeExtraction(){
  if(!window.resumeFileToUpload) return;

  const statusEl = document.getElementById('resumeUploadStatus');
  const submitBtn = document.getElementById('resumeSubmitBtn');

  statusEl.textContent = 'Extracting from your resume…';
  statusEl.style.color = '#8a93a6';
  submitBtn.disabled = true;

  try{
    const formData = new FormData();
    formData.append('file', window.resumeFileToUpload);

    const response = await fetch('/api/extract-from-resume' + costAttributionQS(), {
      method: 'POST',
      body: formData
    });

    if(!response.ok){
      throw new Error(`Server returned ${response.status}`);
    }

    const data = await response.json();
    const extractedText = data.extracted_text || '';

    if(!extractedText.trim()){
      statusEl.textContent = '⚠️ No relevant information found in resume';
      statusEl.style.color = '#d64545';
      submitBtn.disabled = false;
      return;
    }

    // Merge into profile using the same synthesis flow
    await mergeIntoProfile(extractedText);

    statusEl.textContent = '✓ Profile updated with your resume information!';
    statusEl.style.color = '#4c6a1a';
    submitBtn.disabled = false;

    // Reset file input
    document.getElementById('resumeFileInput').value = '';
    window.resumeFileToUpload = null;
    submitBtn.style.display = 'none';

  }catch(e){
    console.error('Resume extraction failed:', e);
    statusEl.textContent = '❌ Failed to extract resume. Try again in a moment.';
    statusEl.style.color = '#d64545';
    submitBtn.disabled = false;
  }
}

// Submit LinkedIn extraction (text paste only)
async function submitLinkedInExtraction(mode){
  const statusEl = document.getElementById('linkedinImportStatus');
  const linkedInInput = document.getElementById('linkedinTextInput').value.trim();

  if(!linkedInInput){
    statusEl.textContent = '⚠️ Please paste your LinkedIn profile text';
    statusEl.style.color = '#d64545';
    return;
  }

  statusEl.textContent = 'Extracting from LinkedIn…';
  statusEl.style.color = '#8a93a6';

  try{
    const response = await fetch('/api/extract-from-linkedin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ linkedin_text: linkedInInput, userid: currentUser?.userid || null })
    });

    if(!response.ok){
      const error = await response.json();
      throw new Error(error.error || `Server returned ${response.status}`);
    }

    const data = await response.json();
    const extractedText = data.extracted_text || '';

    if(!extractedText.trim()){
      statusEl.textContent = '⚠️ No relevant information found in LinkedIn profile';
      statusEl.style.color = '#d64545';
      return;
    }

    // Merge into profile
    await mergeIntoProfile(extractedText);

    statusEl.textContent = '✓ Profile updated with your LinkedIn information!';
    statusEl.style.color = '#4c6a1a';

    // Reset inputs
    document.getElementById('linkedinTextInput').value = '';

  }catch(e){
    console.error('LinkedIn extraction failed:', e);
    statusEl.textContent = `❌ ${e.message}`;
    statusEl.style.color = '#d64545';
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

// Renders the Fresh Finds landing card (stage 0). Reflects only the "nothing to show yet"
// states — no profile, or a profile too thin to match well — styled to match the Profile
// ("My Vibe") tab: white card-soft container, navy heading, gray-muted body copy, orange
// pill CTA. When the profile IS sufficient this is intentionally a no-op (see
// maybeAutoSuggestFreshFinds(), which is what actually renders results into stage-2) —
// callers that just want stage-0's static content refreshed (login, profile edits) can
// call this safely without triggering a search.
function renderSuggestEntryCard(){
  const el = document.getElementById('suggestEntryCard');
  if(!el) return;
  const panel = document.getElementById('browsePanel');
  const browseOpen = !!(panel && !panel.classList.contains('hidden'));
  const btn = document.getElementById('browseToggleBtn');
  if(btn) btn.textContent = browseOpen ? 'Hide opportunity types' : 'Click here to browse opportunities';

  const hasProfile = !!studentProfile.synthesized;
  const sufficient = hasProfile && countProfileWords(studentProfile.synthesized) >= PROFILE_SUFFICIENT_LENGTH;

  if(!sufficient){
    // Profile is insufficient (or empty) — show the appropriate card
    el.innerHTML = hasProfile ? insufficientProfileCardHTML() : emptyProfileCardHTML();
    return;
  }

  // Profile is sufficient — show results if available, otherwise loading
  // Matches already found this session (e.g. the student clicked "Back to Fresh
  // Finds" from stage-2) — offer a one-click way back instead of silently re-running
  // the search, and instead of leaving a stale "Finding your matches…" spinner behind.
  // If no results yet, maybeAutoSuggestFreshFinds() is already in flight (or about to
  // be) and will overwrite this with the same loading card.
  el.innerHTML = currentResults.length ? readyToViewCardHTML() : freshFindsLoadingCardHTML();
}
function readyToViewCardHTML(){
  return `
    <div class="max-w-xl w-full">
      <h2 class="font-heading font-bold text-3xl" style="color: #1d4e89;">Your matches are ready</h2>
      <p class="text-base leading-relaxed italic mt-4 w-full" style="color: #4A6685;">Based on everything in your profile.</p>
    </div>
    <button class="mt-6 pop-btn font-bold px-6 py-3 text-white" style="background-color: #f79256; border: none; cursor: pointer; border-radius: 999px;" onclick="goStage(2)">View my matches →</button>
  `;
}
// Shared by both "nothing to show yet" states — same visual pattern, different
// heading/copy/CTA label depending on whether a (too-thin) profile exists at all.
function emptyProfileCardHTML(){
  return `
    <div class="max-w-xl w-full">
      <h2 class="font-heading font-bold text-3xl" style="color: #1d4e89;">Your profile is empty</h2>
      <p class="text-base leading-relaxed italic mt-4 w-full" style="color: #4A6685;">Every match here gets better once we know you. Takes 2 minutes — add a few things and your matches will show up right here.</p>
    </div>
    <button class="mt-6 pop-btn font-bold px-6 py-3 text-white" style="background-color: #f79256; border: none; cursor: pointer; border-radius: 999px;" onclick="goToProfileChat()">Build my profile</button>
  `;
}
function insufficientProfileCardHTML(){
  return `
    <div class="max-w-xl w-full">
      <h2 class="font-heading font-bold text-3xl" style="color: #1d4e89;">I don't have enough yet to match opportunities</h2>
      <p class="text-base leading-relaxed italic mt-4 w-full" style="color: #4A6685;">Help me help you by building your profile</p>
    </div>
    <button class="mt-6 pop-btn font-bold px-6 py-3 text-white" style="background-color: #f79256; border: none; cursor: pointer; border-radius: 999px;" onclick="openStoryDrawer()">Deepen your story</button>
  `;
}
function freshFindsLoadingCardHTML(){
  return `
    <div class="max-w-xl flex items-center gap-3">
      <span class="spin inline-block w-6 h-6 border-2 rounded-full animate-spin shrink-0" style="border-color: #f4791d; border-top-color: transparent;"></span>
      <div>
        <h2 class="font-heading font-extrabold text-2xl" style="color: #1d4e89;">Finding your matches…</h2>
        <p class="text-sm mt-1" style="color: #4A6685;">Searching based on everything in your profile.</p>
      </div>
    </div>
  `;
}
function freshFindsErrorCardHTML(message){
  return `
    <div class="max-w-xl">
      <h2 class="font-heading font-extrabold text-3xl mb-3" style="color: #1d4e89;">Couldn't load your matches</h2>
      <p class="text-sm" style="color: #4A6685;">${escapeHtmlTracker(message || 'Something went wrong — try again, or browse opportunities by type below.')}</p>
    </div>
    <button class="mt-6 pop-btn bg-white font-bold px-6 py-3 rounded-xl" style="border: 2px solid #1d4e89; color: #1d4e89;" onclick="renderSuggestEntryCard()">Try again</button>
  `;
}
// Called when landing on Fresh Finds (stage 0) with a fresh session (see showPage()).
// If the profile is sufficient, silently runs the same multi-kind match used by the old
// "Profile looks good" confirm step — no extra click — and lands directly on stage-2 with
// the results. Otherwise leaves stage-0 showing the empty/insufficient card so the CTA to
// build the profile (or the "prefer to browse" link right below it) stays front and center.
async function maybeAutoSuggestFreshFinds(){
  const hasProfile = !!studentProfile.synthesized;
  const sufficient = hasProfile && countProfileWords(studentProfile.synthesized) >= PROFILE_SUFFICIENT_LENGTH;
  if(!sufficient) return;
  // Already have matches from earlier this session (e.g. stepped back to stage-0 via
  // "Back to Fresh Finds") — don't silently re-run the search, let the "ready" card
  // rendered by renderSuggestEntryCard() offer a one-click way back to them instead.
  if(currentResults.length) return;
  await runFreshFindsAutoSearch();
}

// Flag to track if we're searching from "My Vibes" — enables untracked filter by default
let searchingFromMyVibes = false;

// Triggered when user clicks "See my matches" CTA in profile page
async function startProfileBasedSearch(){
  searchingFromMyVibes = true;
  // Force a fresh search instead of showing stale cached results (maybeAutoSuggestFreshFinds
  // skips re-searching when currentResults is already populated, and showPage() skips it
  // entirely when currentStage isn't 0) — otherwise the untracked-filter default below never
  // gets applied because resetResultFilters() never runs.
  currentResults = [];
  currentStage = 0;
  showPage('wizard');
}

// Core of the profile-based auto-match — mirrors runProfileSuggestSearch()'s search logic,
// but reports progress/errors into stage-0's suggestEntryCard (runProfileSuggestSearch
// reports into stage-suggest's own status/error elements, which aren't visible here since
// this flow skips that intermediate review stage entirely).
async function runFreshFindsAutoSearch(){
  const el = document.getElementById('suggestEntryCard');
  if(el) el.innerHTML = freshFindsLoadingCardHTML();
  const description = studentProfile.synthesized;
  try{
    // Subjects + grade come from the profile's stored filter values, recomputed only
    // when the profile itself meaningfully changes (see getProfileFilterValues) — this
    // used to be an unconditional Gemini call on every Fresh Finds load.
    const { subjects, grade: studentGrade } = await getProfileFilterValues();
    // Each kind's ranking call is independent — isolate failures per-kind (a single
    // flaky/malformed Gemini response for one kind used to reject the whole Promise.all
    // and blank out every other kind's already-successful results too) so one bad call
    // just contributes an empty list instead of failing the entire search.
    const perKind = await Promise.all(ACTIVE_KINDS.map(async kind => {
      const cfg = KIND_CONFIG[kind];
      if(!cfg) return [];
      try{
        const pool = preFilter(description, subjects, cfg.dbTypes, cfg.strictType, studentGrade);
        const ranked = await rankCandidates(description, pool, '', cfg.strictType);
        const byId = {};
        pool.forEach(o => { byId[o.id] = o; });
        return ranked.filter(r => byId[r.id]).map(r => ({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','look'].includes(r.tier) ? r.tier : 'look', kind }));
      }catch(err){
        console.error(`Ranking failed for kind "${kind}":`, err);
        return [];
      }
    }));
    const merged = perKind.flat();
    if(!merged.length){
      throw new Error('No matches came back — try adding more detail to your profile, or browse by type instead.');
    }
    currentResults = merged;
    selectedIds = new Set();
    resetResultFilters();
    unlocked[2] = true;
    resultsBackTarget = 0;
    renderResults();
    goStage(2);
  }catch(err){
    console.error('Fresh Finds auto-search failed:', err);
    if(el) el.innerHTML = freshFindsErrorCardHTML(err.message);
  }
}
function toggleBrowsePanel(){
  const panel = document.getElementById('browsePanel');
  const btn = document.getElementById('browseToggleBtn');
  if(!panel) return;
  const willOpen = panel.classList.contains('hidden');
  panel.classList.toggle('hidden', !willOpen);
  if(btn) btn.textContent = willOpen ? 'Hide opportunity types' : 'Click here to browse opportunities';
  if(willOpen) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

let suggestPendingQuestions = [];
let suggestAssessedKinds = [];
// Fires when the student hits "Profile looks good" on stage 0 — transitions to the
// stage-suggest view where profile summary is displayed (for re-review if needed) and
// progress status is shown as the readiness check + search runs.
async function startProfileSuggest(){
  if(!studentProfile.synthesized) return;
  goStage('suggest');
  const reviewSection = document.getElementById('suggestProfileReviewSection');
  const progressSection = document.getElementById('suggestProgressSection');
  if(reviewSection) reviewSection.style.display = '';
  if(progressSection) progressSection.style.display = 'none';
  renderSuggestProfileSummary();
  document.getElementById('suggestQuestionsWrap').innerHTML = '';
  document.getElementById('suggestContinueBtn').style.display = 'none';
  document.getElementById('suggestError').classList.remove('show');
  const statusEl = document.getElementById('suggestStatus');
  if(statusEl) statusEl.textContent = 'Reviewing your profile…';
  try{
    const assessment = await assessProfileReadiness(studentProfile.synthesized);
    if(assessment && assessment.ready === false && Array.isArray(assessment.questions) && assessment.questions.length){
      if(statusEl) statusEl.textContent = 'A couple quick questions will help narrow this down:';
      renderSuggestQuestions(assessment.questions.slice(0, 3));
    }else{
      suggestAssessedKinds = (assessment && Array.isArray(assessment.kinds) && assessment.kinds.length)
        ? assessment.kinds.filter(k => ACTIVE_KINDS.includes(k))
        : ACTIVE_KINDS.slice();
      if(statusEl) statusEl.textContent = '';
      await runProfileSuggestSearch();
    }
  }catch(err){
    console.error('Profile readiness check failed:', err);
    // Graceful fallback — don't block the student on a failed assessment call, search all active kinds.
    suggestAssessedKinds = ACTIVE_KINDS.slice();
    if(statusEl) statusEl.textContent = '';
    await runProfileSuggestSearch();
  }
}
// Render profile summary in the stage-suggest review section so user can see what we're
// about to search with (and have the option to go back and edit it).
function renderSuggestProfileSummary(){
  const el = document.getElementById('suggestProfileSummary');
  if(!el) return;
  el.innerHTML = `<div class="bg-indigo-50 border-2 border-slate-900 rounded-2xl p-4 sm:p-6">${profileSummaryBodyHTML(studentProfile.synthesized)}</div>`;
}
// Called from "Profile looks good — find opportunities →" button on stage-suggest's
// profile review section to transition into progress view and kick off the search.
async function confirmSuggestProfile(){
  const reviewSection = document.getElementById('suggestProfileReviewSection');
  const progressSection = document.getElementById('suggestProgressSection');
  if(reviewSection) reviewSection.style.display = 'none';
  if(progressSection) progressSection.style.display = '';
  const statusEl = document.getElementById('suggestStatus');
  if(statusEl) statusEl.textContent = 'Reviewing your profile…';
  try{
    const assessment = await assessProfileReadiness(studentProfile.synthesized);
    if(assessment && assessment.ready === false && Array.isArray(assessment.questions) && assessment.questions.length){
      if(statusEl) statusEl.textContent = 'A couple quick questions will help narrow this down:';
      renderSuggestQuestions(assessment.questions.slice(0, 3));
    }else{
      suggestAssessedKinds = (assessment && Array.isArray(assessment.kinds) && assessment.kinds.length)
        ? assessment.kinds.filter(k => ACTIVE_KINDS.includes(k))
        : ACTIVE_KINDS.slice();
      if(statusEl) statusEl.textContent = '';
      await runProfileSuggestSearch();
    }
  }catch(err){
    console.error('Profile readiness check failed:', err);
    suggestAssessedKinds = ACTIVE_KINDS.slice();
    if(statusEl) statusEl.textContent = '';
    await runProfileSuggestSearch();
  }
}
async function assessProfileReadiness(profileText){
  const kindList = ACTIVE_KINDS.map(k => `"${k}" (${KIND_CONFIG[k].name}: ${KIND_CONFIG[k].desc})`).join(', ');
  const system = `You help decide whether a student's profile has enough detail to confidently recommend extracurricular opportunities, and which types are relevant. Valid opportunity type keys: ${kindList}. Read the profile below. If it gives clear enough signal about what the student wants to do and why, respond with ONLY raw JSON, no markdown, no preamble: {"ready":true,"kinds":["one or more of the valid type keys, the ones genuinely relevant"]}. If it's too vague, sparse, or ambiguous to match well, respond with ONLY raw JSON matching: {"ready":false,"questions":["a short, specific clarifying question", "..."]}. Ask at most 3 questions, and only ones that would actually change which opportunities fit — don't ask generic questions the profile already answers.`;
  return callGeminiJSON(system, profileText, false);
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
    statusEl.textContent = 'Understanding your profile…';
    // Subject inference depends only on the profile text, not on which kind is being
    // searched, so one value serves every kind below (this used to be re-asked once per
    // kind — the slowest part of a multi-kind Suggest search).
    // No explicit grade dropdown on this flow (it's driven by the saved profile, not the
    // Finder form) — the grade comes from whatever grade-level language the student's own
    // profile text happens to contain, if any. Both values are cached on the profile and
    // only recomputed when it meaningfully changes (see getProfileFilterValues).
    const { subjects, grade: studentGrade } = await getProfileFilterValues();

    statusEl.textContent = kinds.length > 1 ? 'Searching every opportunity type…' : `Searching ${KIND_CONFIG[kinds[0]] ? KIND_CONFIG[kinds[0]].name.toLowerCase() + 's' : 'opportunities'}…`;
    // Each kind's ranking call is independent of the others — run them concurrently
    // instead of one at a time, so wall-clock time is bounded by the slowest single
    // call instead of the sum of all of them. Failures are isolated per-kind (a single
    // flaky/malformed Gemini response for one kind used to reject the whole Promise.all
    // and blank out every other kind's already-successful results too) so one bad call
    // just contributes an empty list instead of failing the entire search.
    const perKind = await Promise.all(kinds.map(async kind => {
      const cfg = KIND_CONFIG[kind];
      if(!cfg) return [];
      try{
        let pool = preFilter(description, subjects, cfg.dbTypes, cfg.strictType, studentGrade);
        if(pool.length < 20){ pool = preFilter(description, subjects, cfg.dbTypes, cfg.strictType, studentGrade); }
        const ranked = await rankCandidates(description, pool, '', cfg.strictType);
        const byId = {};
        pool.forEach(o => { byId[o.id] = o; });
        return ranked.filter(r => byId[r.id]).map(r => ({ opp: byId[r.id], reason: r.reason || '', tier: ['strong','look'].includes(r.tier) ? r.tier : 'look', kind }));
      }catch(err){
        console.error(`Ranking failed for kind "${kind}":`, err);
        return [];
      }
    }));
    const merged = perKind.flat();
    if(!merged.length){
      throw new Error('No matches came back — try adding more detail to your profile, or browse by type instead.');
    }
    currentResults = merged;
    selectedIds = new Set();
    resetResultFilters();
    statusEl.textContent = '';
    renderResults();
    unlocked[2] = true;
    resultsBackTarget = 0;
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
let cardHoverTimers = new Map(); // { [oppId]: timeoutId } for 2-second hover expansion

// Called on mouseenter of a result card: starts 2-second timer to expand summary
function startCardExpand(oppId){
  if(cardHoverTimers.has(oppId)) return; // Timer already running
  const timerId = setTimeout(() => {
    const summaryEl = document.getElementById(`summary-${oppId}`);
    if(summaryEl) summaryEl.classList.remove('line-clamp-3');
    cardHoverTimers.delete(oppId);
  }, 2000);
  cardHoverTimers.set(oppId, timerId);
}

// Called on mouseleave of a result card: cancels timer and collapses summary back to 3 lines
function cancelCardExpand(oppId){
  const timerId = cardHoverTimers.get(oppId);
  if(timerId){
    clearTimeout(timerId);
    cardHoverTimers.delete(oppId);
  }
  const summaryEl = document.getElementById(`summary-${oppId}`);
  if(summaryEl) summaryEl.classList.add('line-clamp-3');
}

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

  // Prefer the explicit dropdown; fall back to whatever grade-level language the student's
  // own description happens to contain (e.g. "I'm a junior…") if they left it blank.
  const studentGrade = grade ? parseGradeLevel(grade) : parseGradeFromText(description);

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
      // No kind currently uses live-web search (Conference/Journal Venue moved to
      // the local database once those types had real catalog rows) — kept as a
      // fallback path in case a future kind needs it.
      progressNote.textContent = 'Searching the web for real venues…';
      findLabel.textContent = 'Searching the web…';
      currentResults = await findVenuesViaWeb(description, cfg, prefsText);
    }else{
      const subjects = await inferSubjects(description);
      progressNote.textContent = `Searching ${OPPORTUNITIES.length.toLocaleString()} opportunities…`;
      findLabel.textContent = 'Searching database…';

      let pool = preFilter(description, subjects, cfg.dbTypes, cfg.strictType, studentGrade);
      if(priceWant === 'free'){ pool = pool.filter(o => o.price === 'Free'); }
      if(formatWant === 'remote'){ pool = pool.filter(o => o.location === 'Remote' || o.location === 'In-Person and Remote'); }
      if(formatWant === 'inperson'){ pool = pool.filter(o => o.location === 'In-Person' || o.location === 'In-Person and Remote'); }
      if(pool.length < 20){
        // fallback: relax price/format filters if too few remain — grade eligibility stays
        // a hard constraint (a program listed as grades 9-12 isn't relaxed for a 7th grader).
        pool = preFilter(description, subjects, cfg.dbTypes, cfg.strictType, studentGrade);
      }

      progressNote.textContent = `Ranking the ${pool.length} closest matches…`;
      findLabel.textContent = 'Ranking best fits…';

      const ranked = await rankCandidates(description, pool, prefsText, cfg.strictType);
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
    if(typeof firebase !== 'undefined' && firebase.analytics) {
      firebase.analytics().logEvent('search_executed', {
        'opportunity_type': cfg.name || selectedKind,
        'result_count': currentResults.length,
        'source': cfg.source || 'local',
        'description_length': description.length
      });
    }
    unlocked[2] = true;
    resultsBackTarget = 1;
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
  const bgClass = 'bg-white';
  const dateNote = o.nextDeadlineISO ? `<span class="bg-white border-2 border-indigo-200 text-slate-900 px-3 py-1.5 rounded-full">Next: ${shortDate(o.nextDeadlineISO)}${o.wasEstimated ? ' (est.)' : ''}</span>` : '';
  // Already-tracked opportunities can't be re-selected — clicking "Save Match" on one
  // would just be silently dropped as a duplicate at add time, so instead we surface a
  // tag pointing back to the Tracker, where any edits belong.
  const actionControl = tracked
    ? `<span class="bg-slate-800 text-white font-bold text-xs px-4 py-2 rounded-full cursor-pointer" onclick="event.stopPropagation(); goToTrackerCard('${tracked.item.id}')">📌 In Quest Log. Make edits there.</span>`
    : `<button class="pop-btn font-extrabold text-xs px-5 py-2.5 rounded-full flex items-center justify-center gap-2 border-2 border-slate-900 ${isSelected ? 'bg-lime-400 text-slate-900' : 'bg-white text-slate-900'}" onclick="event.stopPropagation(); toggleSelect('${o.id}')">
            ${isSelected ? '⭐ Saved Match' : '⭐ Save Match'}
         </button>`;

  return `
    <div class="pop-card result-card-clickable ${bgClass} rounded-3xl p-5 sm:p-6 space-y-4 ${isSelected ? 'border-4 border-lime-400 bg-lime-50' : 'border-4 border-slate-900'}" id="result-${o.id}" onmouseenter="startCardExpand('${o.id}')" onmouseleave="cancelCardExpand('${o.id}')" onclick="window.open('${o.url}', '_blank')">
      <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2">
         <div class="flex flex-wrap gap-2">
            <span class="bg-violet-200 text-violet-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">${kindBadge}</span>
            ${r.tier === 'strong' ? `<span class="bg-yellow-300 border-2 border-slate-900 font-extrabold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">⭐ Strong Fit</span>` : `<span class="bg-slate-100 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">Worth a look</span>`}
            ${reviewBadgeHTML(o.review_status, o.review_summary, `result-${o.id}`)}
         </div>
         ${actionControl}
      </div>
      <div>
        <h3 class="font-heading text-2xl sm:text-3xl font-extrabold text-slate-900"><a href="${o.url}" target="_blank" class="hover:underline" onclick="event.stopPropagation()">${o.name}</a></h3>
      </div>
      ${r.aiReasoning ? `<div class="flex gap-3 items-stretch">
        <div class="w-1 rounded-full bg-indigo-400 shrink-0"></div>
        <div class="flex-1">
          <p class="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Profile match ${r.aiRank ? `• Rank #${r.aiRank}` : ''}</p>
          <p class="font-heading text-lg sm:text-xl font-bold text-slate-900 leading-snug">${r.aiReasoning}</p>
        </div>
      </div>` : ''}
      ${r.reason && !r.aiReasoning ? `<div class="flex gap-3 items-stretch">
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
      ${o.summary ? `<p id="summary-${o.id}" class="text-sm text-slate-500 font-medium leading-relaxed line-clamp-3">${o.summary}</p>` : ''}
    </div>
  `;
}
// ---------- Result filters (type / cost / season / format) ----------
let resultFilters = { type: new Set(), price: new Set(), location: new Set(), season: new Set(), profileTags: new Set(), untracked: false };
let resultVisibleCount = 10;
// Bumped by every renderResults() call, so an async filter-bar top-up can tell whether its
// render is still the one on screen before repainting.
let resultsRenderToken = 0;
const RESULT_FILTER_FIELDS = [
  { key: 'type', field: 'type', label: 'Type' },
  { key: 'price', field: 'price', label: 'Cost' },
  { key: 'season', field: 'season', label: 'Season' },
  { key: 'location', field: 'location', label: 'Format' }
];

// ---------- Profile filter tags (the "Your Profile" facet on the results bar) ----------
// These are derived from the profile text exactly like subjects/grade are, so they're
// stored on the profile and computed when it changes — never on a results render. Read
// them via getProfileFilterTags(), or cachedProfileFilterTags() where blocking isn't an
// option (see PROFILE_DERIVED_SLOTS).
//
// Extraction and enrichment are two calls, not one per tag: enriching each tag in its own
// request made a results page cost 1 + N calls (a 10-tag profile = 11 round trips, the
// slowest of which gated the whole filter bar). The model does the same per-tag work either
// way, so they go in one request and come back keyed by tag.
const PROFILE_TAG_LIMIT = 10;

async function extractProfileTagStrings(text){
  const system = `You are extracting specific interests, goals, and pursuits from a student's profile to create concise filter tags. Extract and create tags for:
1. Active passion projects and research projects (what they're currently building/researching)
2. Deep interests they want to explore further (areas they're curious about and want to learn more in)
3. Dormant interests (things they're interested in but haven't started pursuing yet)
4. Academic goals (winning competitions, achieving certifications, mastering skills)
5. Career aspirations (wanting to work at specific companies, roles, or industries)

For each item, create a short, specific tag (max 60 characters) that captures what they want or are doing. Use action-oriented language where possible. Return at most ${PROFILE_TAG_LIMIT} tags, most important first. Return ONLY a JSON array of strings, one tag per item, with no other text or formatting.
Example format: ["Building AI chatbots", "Wants to win Math Olympiad", "Interested in deep learning", "Wants to intern at Google", "Learning quantum computing"]`;

  const userContent = `Extract all interests, goals, and pursuits from this student profile:\n\n${text}`;

  // callGeminiJSON, not callGemini + extractJSON: it retries once on a parse failure. Gemini
  // intermittently emits a stray character into an otherwise fine array (an observed run
  // opened with `[=` instead of `["`), and without the retry that one glitch silently cost
  // the student their whole filter facet — which is exactly what it was written for.
  const tags = await callGeminiJSON(system, userContent, false);
  if(!Array.isArray(tags)) return [];
  return tags.filter(t => typeof t === 'string' && t.trim()).map(t => t.trim()).slice(0, PROFILE_TAG_LIMIT);
}

// One call for every tag. Only asks for the three fields anything actually consumes —
// batchScoreOpportunitiesWithAI() reads `tag`, `intent` and `nextSteps` and nothing else.
// The category/semanticKeywords/opportunityTypes/matchReason this used to request were read
// only by dead code, and on a long profile they made the response several times larger than
// it needed to be, for no behaviour, with a real risk of hitting the token limit mid-array.
async function enrichProfileTags(tags){
  if(!tags.length) return [];

  const system = `You are helping match a high school student's interests/goals to the best opportunities. You will be given a list of the student's profile tags. Analyze EACH tag for what it represents and what would best help them grow.

Return ONLY a JSON array with one object per tag, in the same order as given, no other text:
[{
  "tag": "the tag string exactly as given",
  "intent": "what they are trying to achieve (1 short sentence)",
  "nextSteps": ["2-3 short, logical milestones, e.g. Master advanced techniques", "Enter competitions"]
}]

Keep every field short — the whole array must fit comfortably in one response.`;

  const userContent = `PROFILE TAGS:\n${tags.map((t, i) => `${i + 1}. ${t}`).join('\n')}\n\nReturn the JSON array of ${tags.length} enrichment objects.`;

  let arr = [];
  try{
    const parsed = await callGeminiJSON(system, userContent, false);
    // Tolerate the model wrapping the array in an object ({"tags": [...]}) — the shape it
    // was asked for is an array, but a wrapper is a formatting slip, not a failed answer.
    arr = Array.isArray(parsed) ? parsed
        : (parsed && typeof parsed === 'object') ? (Object.values(parsed).find(Array.isArray) || []) : [];
  }catch(e){
    console.warn('Profile tag enrichment failed, falling back to bare tags:', e.message);
  }

  // Match on the echoed tag string where there is one, falling back to position — a
  // truncated or reordered response still yields usable enrichments for the tags it did
  // return, instead of throwing all of them away.
  const byTag = {};
  arr.forEach((e, i) => {
    if(!e || typeof e !== 'object') return;
    const tag = (typeof e.tag === 'string' && tags.includes(e.tag)) ? e.tag : tags[i];
    if(tag && !byTag[tag]) byTag[tag] = Object.assign({}, e, { tag });
  });
  // Every extracted tag survives, enriched or not. Enrichment only sharpens the scoring
  // prompt — batchScoreOpportunitiesWithAI() already substitutes for a missing intent or
  // nextSteps — so dropping un-enriched tags traded a slightly weaker facet for no facet
  // at all, which is how one malformed response emptied the whole dropdown.
  return tags.map(t => byTag[t] || { tag: t });
}

// Extraction → enrichment, as one unit. Never throws: the tag facet is an enhancement on
// top of a results list that is already on screen, so a failure here hides the facet rather
// than failing anything the student is actually looking at.
async function buildProfileFilterTags(text){
  try{
    const tags = await extractProfileTagStrings(text);
    if(!tags.length) console.warn('Profile tag extraction returned no tags — the filter facet will be hidden.');
    return await enrichProfileTags(tags);
  }catch(e){
    console.error('Error building profile filter tags:', e);
    return [];
  }
}

// (A local keyword/type scorer against an enriched tag used to live here. It had no callers
// — the tag filter goes through batchScoreOpportunitiesWithAI() — and it was the only reader
// of the enrichment fields the prompt above no longer asks for, so it went with them.)

function resetResultFilters(){
  Object.values(resultFilters).forEach(s => { if(s instanceof Set) s.clear(); });
  resultFilters.untracked = searchingFromMyVibes; // Enable untracked filter by default when from My Vibes
  searchingFromMyVibes = false; // Reset flag after using it
  resultVisibleCount = 10;
}

// Score all opportunities against a profile tag in a single Gemini call
// Returns { id, rank, reasoning } for matched opportunities (poor matches omitted by AI)
async function batchScoreOpportunitiesWithAI(enrichedTag, opportunities){
  if(!enrichedTag || !opportunities.length) return [];

  // Format opportunities for Gemini
  const oppsList = opportunities.map(opp =>
    `ID: ${opp.id} | Name: ${opp.name} | Type: ${opp.type} | Summary: ${opp.summary || '(no description)'}`
  ).join('\n');

  const system = `You are helping a student find opportunities that match their interests and goals. Write directly to them in second person (using "you").`;

  const userContent = `STUDENT'S PROFILE TAG: "${enrichedTag.tag}"
INTENT: ${enrichedTag.intent || '(no intent specified)'}
NEXT STEPS: ${(enrichedTag.nextSteps || []).join(', ') || '(no specific steps)'}

OPPORTUNITIES TO RANK:
${oppsList}

Rank these opportunities by relevance to this student's profile. Return JSON array with only genuinely relevant opportunities:
[
  { "id": "opp_id", "rank": 1, "reasoning": "Brief 1-sentence message directly to the student using 'you' language" },
  { "id": "opp_id", "rank": 2, "reasoning": "Brief 1-sentence message directly to the student using 'you' language" },
  ...
]

For each reasoning, write directly to the student as if you're the app speaking to them. Example: "This aligns perfectly with your interest in AI competitions" or "You can showcase your skills here". Omit opportunities that don't align with the profile. Include only good/strong matches.
Return ONLY valid JSON, no markdown, no preamble.`;

  try {
    const response = await callGemini(system, userContent, false);
    const results = extractJSON(response) || [];
    return results;
  } catch(e) {
    console.error('Batch scoring failed:', e);
    return []; // Return empty on error — filterResultList will fall back to keyword matching
  }
}

function opportunityMatchesProfileTag(opp, tag){
  // Precise matching: look for key concepts from the project in the opportunity description
  const oppText = `${opp.name} ${opp.org} ${opp.summary}`.toLowerCase();
  const tagLower = tag.toLowerCase();

  // Direct substring match (exact project name/phrase)
  if(oppText.includes(tagLower)) return true;

  // Extract key domain words (nouns, specific terms) - at least 3 chars, exclude very common words
  const stopWords = new Set(['and', 'the', 'for', 'with', 'from', 'that', 'this', 'are', 'was', 'using', 'app', 'project', 'students', 'investigating', 'current']);
  const words = tagLower.split(/\s+/).filter(w => w.length > 2 && !stopWords.has(w));

  if(words.length === 0) return false;

  // Look for key concepts - at least 2 matching words from the project tag
  const matches = words.filter(word => oppText.includes(word));

  return matches.length >= Math.min(2, Math.max(1, Math.ceil(words.length / 2)));
}

async function filterResultList(list){
  // First apply field-based filters (type, price, season, location)
  let filtered = list.filter(r => RESULT_FILTER_FIELDS.every(f => {
    const set = resultFilters[f.key];
    return !set.size || set.has(r.opp[f.field]);
  }));

  // Apply AI-powered profile tag filter if enabled
  if(resultFilters.profileTags.size > 0){
    const selectedTagStrings = Array.from(resultFilters.profileTags);

    // Get the single selected enriched tag (single-select enforced by toggleResultFilter).
    // A tag can only be selected from a bar rendered off these same stored values, so they
    // are present here by construction — but the profile can be edited from another tab
    // mid-session, so a miss just means no tag filter rather than an error.
    const selectedEnrichedTag = (cachedProfileFilterTags() || []).find(et =>
      selectedTagStrings.includes(et.tag)
    );

    if(selectedEnrichedTag){
      // Call Gemini to score all opportunities at once
      const aiResults = await batchScoreOpportunitiesWithAI(selectedEnrichedTag, filtered.map(r => r.opp));

      // Create a map of ID -> reasoning for quick lookup
      const aiScores = {};
      aiResults.forEach(result => {
        aiScores[result.id] = { reasoning: result.reasoning, rank: result.rank };
      });

      // Filter to only opportunities returned by AI (AI is saying "omit poor matches")
      // and attach reasoning
      filtered = filtered
        .filter(r => aiScores[r.opp.id]) // Only keep opps that AI rated
        .map(r => {
          r.aiReasoning = aiScores[r.opp.id].reasoning;
          r.aiRank = aiScores[r.opp.id].rank;
          return r;
        })
        .sort((a, b) => a.aiRank - b.aiRank); // Sort by AI rank order
    }
  }

  // Apply untracked filter if enabled (after AI filtering)
  if(resultFilters.untracked){
    filtered = filtered.filter(r => !findTrackedItem(r.opp));
  }
  return filtered;
}
// Synchronous — `enrichedTags` comes from the caller (the stored profile record), and
// `tagsPending` is true only while they're still being computed for a profile that has
// none yet. This used to await the tag extraction itself, which is what put an
// "Analyzing your profile…" spinner in front of results that were already in hand.
function renderResultFilterBar(list, enrichedTags, tagsPending){
  const wrap = document.getElementById('resultFilterWrap');
  const bar = document.getElementById('resultFilterBar');
  if(!wrap || !bar) return;
  let anyFacet = false;
  let html = '';

  // The profile-tag facet, once the enriched tags for the current profile exist. While
  // they don't, show the placeholder in its place — the rest of the bar stays usable.
  if(tagsPending){
    anyFacet = true;
    html += `
      <div class="flex items-center gap-2 py-2 px-3">
        <span class="inline-block w-4 h-4 border-2 rounded-full border-indigo-300 border-t-indigo-600 animate-spin"></span>
        <span class="text-sm text-slate-600">Reading your profile…</span>
      </div>
    `;
  }
  if(enrichedTags.length > 0){
    anyFacet = true;
    const panelId = 'resultFilterPanel_profileTags';
    const activeCount = resultFilters.profileTags.size;
    html += `
      <div class="relative nav-dropdown">
        <button class="pop-btn bg-white font-bold text-xs px-3 py-2 rounded-xl flex items-center gap-1" onclick="toggleNavDropdownPanel('${panelId}')">
          <span>▾</span> Your Profile${activeCount ? ` (${activeCount})` : ''}
        </button>
        <div class="absolute left-0 top-full mt-2 w-56 bg-white p-3 rounded-2xl z-50 hidden nav-dropdown-panel border-2 border-slate-900" id="${panelId}">
          <div class="space-y-1">
            <label class="flex items-center gap-2 text-xs font-medium py-1 cursor-pointer">
              <input type="radio" name="profileTagFilter" ${resultFilters.profileTags.size === 0 ? 'checked' : ''} onchange="resultFilters.profileTags.clear(); resultVisibleCount = 10; renderResults();">
              None
            </label>
            ${enrichedTags.map(enriched => `
              <label class="flex items-center gap-2 text-xs font-medium py-1 cursor-pointer">
                <input type="radio" name="profileTagFilter" ${resultFilters.profileTags.has(enriched.tag) ? 'checked' : ''} onchange="toggleResultFilter('profileTags', '${enriched.tag}')">
                ${enriched.tag}
              </label>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }

  // Add untracked filter toggle
  html += `
    <label class="flex items-center gap-2 text-xs font-bold py-1 px-3 bg-white rounded-xl border-2 border-slate-900 cursor-pointer">
      <input type="checkbox" ${resultFilters.untracked ? 'checked' : ''} onchange="toggleUntrackedFilter()">
      Only untracked
    </label>
  `;

  // Add other filters
  RESULT_FILTER_FIELDS.forEach(f => {
    const values = [...new Set(list.map(r => r.opp[f.field]).filter(Boolean))].sort();
    if(values.length < 2) return;
    anyFacet = true;
    const panelId = 'resultFilterPanel_' + f.key;
    const activeCount = resultFilters[f.key].size;
    html += `
      <div class="relative nav-dropdown">
        <button class="pop-btn bg-white font-bold text-xs px-3 py-2 rounded-xl flex items-center gap-1" onclick="toggleNavDropdownPanel('${panelId}')">
          <span>▾</span> ${f.label}${activeCount ? ` (${activeCount})` : ''}
        </button>
        <div class="absolute left-0 top-full mt-2 w-56 bg-white p-3 rounded-2xl z-50 hidden nav-dropdown-panel border-2 border-slate-900" id="${panelId}">
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
  });

  bar.innerHTML = html;
  const anyActive = Object.values(resultFilters).some(s => s instanceof Set ? s.size : s);
  bar.insertAdjacentHTML('beforeend', anyActive ? `<button class="text-xs font-bold text-indigo-600 hover:underline" onclick="clearResultFilters()">Clear filters</button>` : '');
  wrap.classList.toggle('hidden', !anyFacet && !resultFilters.untracked);
}
function toggleResultFilter(key, value){
  const set = resultFilters[key];

  // Profile tags are single-select: clear others when selecting a new one
  if(key === 'profileTags'){
    if(set.has(value)){
      set.delete(value);
    } else {
      set.clear();
      set.add(value);
    }
  } else {
    // Other filters allow multiple selections
    if(set.has(value)) set.delete(value); else set.add(value);
  }

  resultVisibleCount = 10;
  renderResults();
}
function toggleUntrackedFilter(){
  resultFilters.untracked = !resultFilters.untracked;
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
async function renderResults(){
  // Already-tracked opportunities float to the very top, then saved matches (clicking
  // "Save Match" visibly moves a card up into this group), then everything else —
  // each group still ordered by tier internally.
  const resultRank = r => findTrackedItem(r.opp) ? 0 : (selectedIds.has(r.opp.id) ? 1 : 2);
  const sorted = [...currentResults].sort((a, b) => {
    const rankDiff = resultRank(a) - resultRank(b);
    if(rankDiff !== 0) return rankDiff;
    return TIER_ORDER[a.tier] - TIER_ORDER[b.tier];
  });

  // Paint the filter bar from the profile's stored tags. On the common path they're
  // already there and this costs nothing; when they aren't (a profile edited seconds ago,
  // or one that predates this cache) the bar shows a placeholder in that one slot and
  // fills itself in below, rather than holding the results back on an API call.
  const renderToken = ++resultsRenderToken;
  const cachedTags = cachedProfileFilterTags();
  const hasProfile = !!studentProfile.synthesized;
  renderResultFilterBar(sorted, cachedTags || [], !cachedTags && hasProfile);

  // Only actually awaits when a profile tag is selected (that's a deliberate,
  // student-triggered scoring call); otherwise this resolves without a round trip.
  const filtered = await filterResultList(sorted);
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

  // Tags weren't ready — compute them (this also persists them onto the profile, so it
  // happens at most once) and swap the placeholder for the real facet when they land. The
  // token check drops a slow response whose render has since been superseded, which would
  // otherwise repaint the bar with a list the visible results no longer come from.
  if(!cachedTags && hasProfile){
    getProfileFilterTags()
      .then(rec => {
        if(renderToken !== resultsRenderToken) return;
        renderResultFilterBar(sorted, rec.enrichedTags, false);
      })
      .catch(err => {
        console.warn('Profile filter tags unavailable:', err.message);
        if(renderToken === resultsRenderToken) renderResultFilterBar(sorted, [], false);
      });
  }
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

// On-demand, cross-user-cached deadline check (see server.py's /api/opportunities/<id>/
// deadline + check_deadlines.py). Server serves cached status/deadlines straight from
// Supabase if last checked under 7 days ago; otherwise runs a fresh Gemini web_search check
// and re-caches it. Never throws — callers fall back to extractTrackerInfo's own guess if
// this fails for any reason (offline, Gemini quota, etc.), so a hiccup here never blocks
// adding to or loading the tracker.
async function fetchDeadlineCheck(oppId){
  try{
    const res = await fetch(`/api/opportunities/${encodeURIComponent(oppId)}/deadline${costAttributionQS()}`);
    if(!res.ok) return null;
    return await res.json();
  }catch(err){
    console.warn('Deadline check request failed:', err);
    return null;
  }
}
// Overlays a fetchDeadlineCheck() result onto an extractTrackerInfo()-shaped info object,
// in place — the shared/cached deadline check is authoritative for status/important_dates/
// was_estimated when present, since (unlike extractTrackerInfo's own per-call guess) it's
// verified server-side and shared across every user tracking the same opportunity.
function applyDeadlineCheckToInfo(info, deadlineInfo){
  if(!deadlineInfo) return;
  if(['running','not_running','unknown'].includes(deadlineInfo.status)) info.status = deadlineInfo.status;
  if(Array.isArray(deadlineInfo.important_dates) && deadlineInfo.important_dates.length) info.important_dates = deadlineInfo.important_dates;
  if(typeof deadlineInfo.was_estimated === 'boolean') info.was_estimated = deadlineInfo.was_estimated;
  if(deadlineInfo.important_date_note) info.note = deadlineInfo.important_date_note;
}

async function extractTrackerInfo(opp){
  const today = todayLabel();
  const thisYear = new Date().getFullYear();
  const nextYear = thisYear + 1;
  const root = baseDomain(opp.url);
  const system = `You extract structured tracking data for an extracurricular opportunity (program, internship, competition, or research position), for a high-school student's tracker. Today's date is ${today}.

YOU MUST use web_search to gather current information before answering — do not rely on training data alone.

GOAL: capture as many pertinent dates as you can find or reasonably estimate. Estimating from a prior cycle is expected and encouraged, not a fallback of last resort — a well-justified estimate is always better than an empty field.

SEARCH STEPS (do all of these, in order):
1. Start with the given URL.
2. Search "site:${root} ${nextYear}" and "site:${root} ${thisYear}" for a current/upcoming-cycle page — orgs often publish a separate year-specific page distinct from the evergreen landing page, which frequently omits the specific dates you actually need.
3. ALWAYS ALSO search for the most recent PAST cycle (e.g. "site:${root} ${thisYear} deadline" and, using the year before ${thisYear} — compute it yourself from ${today} — "site:${root} <that year>"), even if step 2 succeeded. This is your estimation basis and is mandatory, not optional: you need it either to confirm the pattern behind a found date or to construct an estimate when nothing current is posted.
4. Search "site:${root} FAQ", "how to apply", "key dates", "deadlines", "timeline" and check the best hits — specific program URLs sometimes point to outdated or archived pages while the org's current site has the live one.
5. Look explicitly for closure language: "cycle closed," "not running this year," "applications no longer accepted," etc. DISTINGUISH between: (a) current cycle is closed but program recurs (e.g., "2026 closed, 2027 opening Fall") → status="running" (the program itself is ongoing), still extract dates for the next cycle; (b) program is permanently discontinued (e.g., "no longer offered," "program ended") → status="not_running", do not estimate future dates. Evidence of recurrence ("Next cycle in Fall", "2027 details TBA", "Check back for 2027") → treat as "running" with forward-dated important_dates.

ESTIMATION LOGIC (single source of truth — apply in this order):
a. Found explicit current/upcoming-cycle dates → use them, was_estimated:false for those entries.
b. No current-cycle dates found, but you found last cycle's real dates AND the program looks recurring (no evidence it's discontinued) → roll each date forward by ~1 year (or to the next plausible occurrence), was_estimated:true, status:"running". This is the expected path when a new cycle's page isn't live yet — use it; don't default to "unknown."
c. Found only a vague pattern (e.g. "opens in fall," "rolling through spring") → construct a concrete estimated date from it (pick a reasonable specific day within the stated window), was_estimated:true, explain the basis briefly in note.
d. Current cycle is explicitly closed (e.g., "2026 applications closed") BUT organization states or implies the program will recur (e.g., "2027 opens Fall 2026") → status:"running", extract/estimate dates for the future cycle from explicit month/season language, was_estimated:true. This is the expected path when a new cycle isn't yet open — capture the forward-looking dates.
e. Found genuinely nothing current AND nothing from any prior cycle after completing all search steps above → status:"unknown". This should be rare — only after step 3 has actually been tried and failed.

Important dates — this matters a lot; capture EVERY pertinent date, not just a single "deadline":
- This includes (when they exist or can be estimated): registration/application opens, early-bird deadline, regular/final deadline, notification/decision date, and event dates (e.g. a conference or symposium's actual start and end dates) — anything relevant in between. Many programs have MORE THAN ONE deadline — e.g. an early-bird/early registration deadline well before a later regular or final deadline (AMC 12's early-bird registration deadline is a good example: it lands weeks before the exam itself). Find and list EVERY distinct date you can, each with a short specific label (e.g. "Early Bird Registration", "Regular Registration", "Final Deadline", "Notification Date", "Conference Begins", "Conference Ends") and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Do not collapse them into just one "final" date — the earliest one is often the one a student needs to act on first.
- Actively search for the OPENS date specifically, not just the close — this is the field most often missed, and it's often the single most useful date for a student trying to plan ahead. Estimate it from the prior cycle if not explicitly posted (was_estimated:true).
- Every date you reason about belongs in "important_dates", not just in "note". If you have enough basis to write a date into "note" (e.g. "registration typically opens Sept and closes Nov"), you have enough basis to ALSO add the matching structured entry to "important_dates" (was_estimated:true) — never describe a date in "note" prose without adding it. "note" is for a short caveat/basis explanation, not a place to put date information that should have been structured.
- Only omit a date category if you found no information for it AND no prior-cycle basis to estimate it.
- If there's genuinely only one date, list just that one entry.

SELF-CHECK before responding:
- Every date in "important_dates" must be on or after today (${today}). If any is in the past, roll it forward to its next real occurrence (was_estimated:true) or drop it — never submit a past date.
- Every specific date/estimate mentioned in "note" must have a matching structured entry in "important_dates", and vice versa — the two must agree.
- Prefer including a reasonably-estimated date over omitting it. Only leave a category out if step (d) above genuinely applies.

Action items — think through what a student would actually need to DO to meet the nearest deadline, not just the deadline itself: e.g. requesting a recommendation letter, drafting an essay, gathering transcripts, preparing a portfolio or writing sample, getting parent/guardian sign-off, registering for a required test. Infer these from the requirements you find and from what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or how to approach its substance, since you have no way of knowing the specifics of their work and must not assume or invent any. List 3-5 short, concrete action items (skip this if status is not_running).
For each action item, also give your best-guess direct URL for where the student would actually go to do it — the specific application/submission portal, payment or fee page, account sign-up/registration page, common-app or portal login, recommender/counselor form, or test-registration page, as applicable. Use the most specific URL you found during search (not just the homepage) whenever one exists. If nothing more specific than the general apply/info URL applies, reuse that URL. Only use null if you genuinely found no plausible page for that action — never invent or guess at a URL path that wasn't actually seen.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON, matching exactly this schema: {"status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format, separated by ' · '","fit":"one sentence, under 25 words, on what this actually involves","note":"one sentence, under 25 words: status/estimate basis/caveat","noteType":"good, plain, or flag — use flag if not_running or a major caveat","important_dates":[{"label":"short specific label, e.g. 'Early Bird Registration'","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other"}],"deadline_label":"short text like ROLLING or TBA — only used when the important_dates array is empty","was_estimated":true or false,"requirements":[{"date":"short date text","text":"under 12 words — what's needed, not a repeat of an important_dates entry"}],"apply_url":"the best URL for actually applying","apply_label":"short button label like 'Apply now'","calendar_events":[{"date":"YYYY-MM-DD","text":"under 8 words","type":"deadline, opens, notify, or conference"}],"action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action (submission portal, payment page, sign-up page, etc.), or null"}]}. Stay well within a 1000-token response: at most 4 important_dates entries, 3 requirements items, 3 calendar_events, and 5 action_items. Never truncate mid-value or leave the JSON unclosed — shorten or drop optional arrays first, but keep at least the earliest date if one exists.`;
  const userContent = `Opportunity: ${opp.name} (${opp.org})\nURL: ${opp.url}\nKnown info: ${opp.summary}\n\nFetch this URL (and the base site if needed), and extract current tracking details per the schema. Look carefully for every relevant date — registration open/close, event dates, notifications — not just the final deadline.`;
  return callGeminiJSON(system, userContent, true);
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
      // Migration: older saved items predate the unified importantDates field — they still
      // carry the old separate opensISO + deadlines shape. Fold both into a single
      // importantDates list (tagging the old opensISO as type 'opens', old deadlines entries
      // as type 'deadline') so every downstream helper (getDisplayMilestones, earliestUpcoming,
      // computeProgressStatus, etc.) only ever has to read one field.
      ALL_BUCKETS.forEach(b => trackerData[b].forEach(item => {
        if(Array.isArray(item.importantDates)) return;
        const dates = [];
        if(item.opensISO) dates.push({ dateISO: item.opensISO, label: 'Opens', type: 'opens' });
        (item.deadlines || []).forEach(d => {
          if(d && d.dateISO) dates.push({ dateISO: d.dateISO, label: d.label || 'Deadline', type: 'deadline' });
        });
        item.importantDates = dates;
        delete item.opensISO;
        delete item.deadlines;
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
  ['home','wizard','tracker','profile','subscription'].forEach(p => {
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
  // When returning to the Finder (wizard), restore the last stage the user was on
  // (e.g. results page) instead of always resetting to stage 0. This preserves
  // search results when navigating away and coming back.
  if(name === 'wizard'){
    if(currentStage === 0){
      // Fresh landing (not restoring a prior search) — show stage-0 immediately (goStage
      // renders the entry card), then silently upgrade to matched results if the profile
      // is sufficient.
      goStage(0);
      maybeAutoSuggestFreshFinds();
    }else{
      // Restore the previous stage (e.g. results) instead of resetting on every visit.
      // goStage() only re-renders the entry card for n===0, so refresh it explicitly here
      // in case the profile changed while away (stage-2's card isn't visible but should
      // stay in sync for when the user does navigate back to stage-0).
      renderSuggestEntryCard();
      goStage(currentStage);
    }
  }
  // Deliberately no initProfileChat() call here — the drawer (and the API call for its
  // starter questions) only opens via openStoryDrawer(), triggered by an explicit
  // "deepen my story" action, not just by visiting this tab.
  if(name === 'profile'){ renderProfile(); renderProfileFit(); }
  if(name === 'subscription'){ checkSubscriptionStatus(); renderSubscriptionPage(); }
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
// Format date as "Mon DD" (e.g., "Nov 13") for tracker card date display
function formatMonthDay(iso){
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric'});
}
// Gathers every known date on an item (each deadline milestone, plus the
// opens date if known) and returns whichever comes soonest that hasn't
// already passed — "the first deadline the system finds," not necessarily
// the final/regular one. Falls back to the latest known date if everything
// has already passed.
function earliestUpcoming(item){
  const candidates = (item.importantDates || [])
    .filter(d => d.dateISO || d.date_iso)
    .map(d => ({ date: d.dateISO || d.date_iso, label: d.label, kind: d.type || 'deadline' }));
  if(!candidates.length) return null;
  const future = candidates.filter(c => daysUntil(c.date) >= 0);
  future.sort((a, b) => a.date.localeCompare(b.date));
  if(future.length) return future[0];
  candidates.sort((a, b) => a.date.localeCompare(b.date));
  return candidates[candidates.length - 1];
}
// Single source of truth for "every date to display" on an item — used by both the
// Tracker's list-view card (trackerCardHTML) and the calendar swimlanes
// (deriveKeyDatesForItems), which previously each read item.opensISO/item.deadlines
// independently and could disagree. Now both read the single item.importantDates list
// (every pertinent date — opens, deadline, event start/end, etc. — each tagged with a
// "type"). Dedupes exact (date, label) duplicates, sorts chronologically, and flags each
// entry `isPast` (rather than silently dropping it) so a stale/un-refreshed date is
// visually distinguishable instead of displaying identically to a real upcoming one.
function getDisplayMilestones(item){
  const seen = new Set();
  const milestones = [];
  (item.importantDates || []).forEach(d => {
    // Handle both dateISO (camelCase) and date_iso (snake_case) since dates may come from different sources
    const dateISO = d.dateISO || d.date_iso;
    if(!dateISO) return;
    const key = dateISO + '|' + (d.label || '');
    if(seen.has(key)) return;
    seen.add(key);
    milestones.push({ date: dateISO, label: d.label || 'Date', type: d.type || 'deadline' });
  });
  milestones.sort((a, b) => a.date.localeCompare(b.date));
  milestones.forEach(m => { m.isPast = daysUntil(m.date) < 0; });
  return milestones;
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
  const dates = (item.importantDates || []).map(d => d.dateISO || d.date_iso).filter(Boolean);
  if(!dates.length) return 'not_started';
  dates.sort();
  const firstStep = dates[0];
  const lastStep = dates[dates.length - 1];
  if(daysUntil(firstStep) > 0) return 'not_started'; // first step (registration/opens) hasn't happened yet
  // If all dates are past, check if this is a running program with estimated dates:
  // For recurring programs, estimated dates for a past cycle don't mean the program is over.
  // Mark as 'not_started' (Future Event) so students know to check for next cycle dates.
  if(daysUntil(lastStep) < 0) {
    if(item.status === 'running' && item.wasEstimated) {
      return 'not_started'; // Recurring program with estimated dates for past cycle — next cycle coming
    }
    return 'completed'; // Confirmed past dates or non-running program
  }
  return 'in_progress';
}
// Opportunity event-timing state pill — always green/orange/red for Happening Now /
// Future Event / Past Event, everywhere in the app (see .status-pill.status-opp-* in styles.css).
function statusPillHTML(status){
  return `<span class="status-pill status-opp-${status}">${PROGRESS_STATUS_LABEL[status]}</span>`;
}
// ---------- Review status badge (check_reviews.py's review_status/review_summary) ----------
// Click-to-reveal: the badge alone just signals the verdict; the full review_summary string
// only shows once the student taps it, as a floating popover anchored to the pill (position:
// absolute, see .review-popover-panel in styles.css) that overlaps the card content below it
// rather than pushing the card taller. Uses the same sage/orange/rose palette as the rest of
// the app's status system (see .status-pill.status-opp-* in styles.css) so "positive" reads
// as the identical sage green used for Finder's Strong Fit cards and Tracker's Happening Now
// state — shown identically on both Finder result cards and Tracker cards. Deliberately built
// from the exact same classes (border-2 border-slate-900, text-[10px], px-3 py-1, rounded-full)
// as the neighboring type/tier badges, not the taller .status-pill class, so it sits at the
// same height as the pills next to it. No icon — text-only, per design direction.
const REVIEW_STATUS_META = {
  positive: { label: 'Well reviewed', cls: 'bg-emerald-100 text-emerald-900' },
  mixed: { label: 'Mixed reviews', cls: 'bg-orange-100 text-orange-900' },
  negative: { label: 'Reported issues', cls: 'bg-rose-100 text-rose-900' }
  // insufficient_data / null / undefined: no independent evidence either way — nothing worth
  // surfacing, so no badge at all rather than a hollow "insufficient data" pill.
};
function reviewBadgeHTML(status, summary, uid){
  const meta = REVIEW_STATUS_META[status];
  if(!meta) return '';
  const safeSummary = summary ? escapeHtmlTracker(summary) : 'No further detail available.';
  return `
    <div class="relative inline-block review-popover-wrap">
      <button type="button" class="${meta.cls} border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full cursor-pointer" onclick="event.stopPropagation(); toggleReviewInfo('${uid}')" title="Tap to see why">${meta.label}</button>
      <div class="review-popover-panel bg-white border-2 border-slate-900 rounded-xl p-3 text-xs text-slate-600 font-medium normal-case" id="review-info-${uid}" onclick="event.stopPropagation()">${safeSummary}</div>
    </div>
  `;
}
function toggleReviewInfo(uid){
  const el = document.getElementById(`review-info-${uid}`);
  if(!el) return;
  const willOpen = !el.classList.contains('open');
  document.querySelectorAll('.review-popover-panel.open').forEach(p => p.classList.remove('open'));
  if(willOpen) el.classList.add('open');
}
document.addEventListener('click', (e) => {
  if(!e.target.closest('.review-popover-wrap')){
    document.querySelectorAll('.review-popover-panel.open').forEach(p => p.classList.remove('open'));
  }
});
// Shared segmented progress bar + legend, used by the Home "Opportunities you are tracking" card
// (kind:'opp' — green/orange/red) and the "Coming up" to-do list (kind:'task' — red/orange/green).
function progressBarHTML(counts, total, labels = PROGRESS_STATUS_LABEL, order = ['in_progress', 'not_started', 'completed'], kind = 'opp'){
  if(!total){
    return { track: '', legend: '<p class="empty-state">Nothing here yet.</p>' };
  }
  const track = order.map(k => `<div class="progress-seg seg-${kind}-${k}" style="width:${(counts[k] / total * 100)}%"></div>`).join('');
  const legend = order.map(k => `
    <span class="progress-legend-item text-xs font-bold" style="color: #1d4e89;">
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
  if(typeof firebase !== 'undefined' && firebase.analytics) {
    firebase.analytics().logEvent('opportunity_saved', {
      'opportunity_id': id,
      'saved': trackerSavedState[id]
    });
  }
  saveTrackerSaved();
  renderTrackerPage();
}
// Permanently removes an item from whichever bucket holds it (and clears its saved-for-later
// flag, if any). Deletes immediately on click — no second confirmation step, unlike
// clearProfile which stays double-click-armed since it wipes the whole profile at once.
// Also updates finder results so the "In Quest Log" tag disappears.
function deleteTrackerItem(id, btn){
  for(const bucket of ALL_BUCKETS){
    const idx = trackerData[bucket].findIndex(i => i.id === id);
    if(idx !== -1){ trackerData[bucket].splice(idx, 1); break; }
  }
  delete trackerSavedState[id];
  saveTrackerData();
  saveTrackerSaved();
  renderTrackerPage();
  // Re-render finder results if they're currently visible to update "In Quest Log" tags
  if(currentStage === 2) renderResults();
}
// Renders an opportunity's deadlines grouped by year, in a single column when short
// or split into two balanced columns (by row count, not strictly by year) once the
// list gets long — matching the Quest Log redesign. When a year's dates span both
// columns, column two gets a "(cont.)" tag instead of repeating a bare year label.
function deadlineRowsHTML(milestones){
  if(!milestones.length) return '';
  const milestonesByYear = {};
  milestones.forEach(m => {
    const year = m.date.slice(0, 4);
    if(!milestonesByYear[year]) milestonesByYear[year] = [];
    milestonesByYear[year].push(m);
  });
  const entries = [];
  Object.keys(milestonesByYear).sort().forEach(year => {
    entries.push({ type: 'tag', year });
    milestonesByYear[year].forEach(m => entries.push({ type: 'date', m }));
  });

  const renderEntry = (e) => e.type === 'tag'
    ? `<div class="date-year-tag">${e.year}${e.cont ? ' (cont.)' : ''}</div>`
    : `<div class="date-row"><span style="font-weight:700;color:#0f1c33;width:52px;flex-shrink:0;">${formatMonthDay(e.m.date)}</span><span style="color:#33404f;">${e.m.label}</span></div>`;
  const renderColumn = (col) => col.map(renderEntry).join('');

  const TWO_COLUMN_THRESHOLD = 5;
  if(entries.length <= TWO_COLUMN_THRESHOLD){
    return `<div>${renderColumn(entries)}</div>`;
  }

  const colSize = Math.ceil(entries.length / 2);
  const col1 = entries.slice(0, colSize);
  const col2 = entries.slice(colSize);
  if(col2.length && col2[0].type !== 'tag'){
    let lastYear = null;
    for(let i = colSize - 1; i >= 0; i--){
      if(entries[i].type === 'tag'){ lastYear = entries[i].year; break; }
    }
    if(lastYear) col2.unshift({ type: 'tag', year: lastYear, cont: true });
  }

  return `<div style="display:grid;grid-template-columns:1fr 1fr;gap:0 24px;">
    <div>${renderColumn(col1)}</div>
    <div>${renderColumn(col2)}</div>
  </div>`;
}
function trackerCardHTML(item, sourceLabel){
  const notRunningBadge = item.status === 'not_running'
    ? `<span class="bg-rose-100 text-rose-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">Not running</span>`
    : '';
  const typeBadge = sourceLabel ? `<span class="bg-violet-200 text-violet-900 border-2 border-slate-900 font-bold text-[10px] uppercase tracking-wider px-3 py-1 rounded-full">${sourceLabel}</span>` : '';
  const estimatedNote = item.wasEstimated && item.status !== 'not_running'
    ? `<div class="bg-yellow-200 border-2 border-slate-900 rounded-xl px-4 py-2.5"><p class="text-xs font-bold text-amber-800">Predicted dates from past cycle.</p></div>`
    : '';
  const milestones = getDisplayMilestones(item);
  const allMilestonesPast = milestones.length > 0 && milestones.every(m => m.isPast);
  const staleWarning = allMilestonesPast
    ? (item.status === 'running'
        ? `<div class="bg-yellow-100 border-2 border-slate-900 rounded-xl px-4 py-2.5"><p class="text-xs font-bold text-amber-800">📅 These dates are from the last cycle. Check the program site for next cycle dates.</p></div>`
        : `<div class="bg-rose-100 border-2 border-slate-900 rounded-xl px-4 py-2.5"><p class="text-xs font-bold text-rose-800">⚠ No upcoming dates — this program's last cycle has ended.</p></div>`)
    : '';

  const deadlineRows = deadlineRowsHTML(milestones);

  const isSaved = !!trackerSavedState[item.id];
  const progress = computeProgressStatus(item);
  // Shown only for the batch of opportunities added in the current session (cleared
  // as soon as the user navigates away from the Tracker screen — see showPage).
  const newBanner = newlyAddedTrackerIds.has(item.id)
    ? `<span class="absolute font-extrabold text-[10px] uppercase z-10" style="left:-8px;top:-8px;background:#d7f542;color:#1a2540;padding:3px 10px;border-radius:8px;border:2px solid #1d4e89;box-shadow:2px 2px 0 #1d4e89;">New</span>`
    : '';

  return `
    <div class="pop-card bg-white rounded-3xl p-5 sm:p-6 space-y-4 border-4 border-slate-900 relative ${item.status === 'not_running' ? 'opacity-60' : ''}" id="tracker-card-${item.id}">
      ${newBanner}
      <div class="flex justify-between items-start gap-2">
        <div class="flex flex-wrap gap-2">
          ${typeBadge}
          ${notRunningBadge}
          ${reviewBadgeHTML(item.reviewStatus, item.reviewSummary, `tracker-${item.id}`)}
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button onclick="event.stopPropagation(); toggleTrackerSaved('${item.id}')" class="icon-btn" style="color:${isSaved ? '#f79256' : '#1d4e89'};" title="${isSaved ? 'Restore' : 'Save'}">${isSaved
            ? `<svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`
            : `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`}</button>
          <button onclick="event.stopPropagation(); deleteTrackerItem('${item.id}', this)" class="icon-btn" style="color:#94a3b8;" title="Delete"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
        </div>
      </div>

      <div>
        <h3 class="font-heading text-2xl sm:text-3xl font-extrabold text-slate-900 leading-tight"><a href="${item.url}" target="_blank" class="hover:underline">${item.name}</a></h3>
        <p class="text-sm text-slate-500 font-medium mt-1 line-clamp-1">${item.meta || ''}</p>
      </div>

      ${estimatedNote}
      ${staleWarning}
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
// Reuses getDisplayMilestones() (same helper the list-view card uses) so the calendar can
// never show a different set of dates than the Tracker cards for the same item.
function deriveKeyDatesForItems(items){
  const dates = [];
  items.forEach(item => {
    if(item.status === 'not_running') return;
    const shortLabel = item.name.length > 22 ? item.name.slice(0, 20) + '…' : item.name;
    getDisplayMilestones(item).forEach(m => {
      dates.push({ date: m.date, label: shortLabel, venueId: item.id, text: m.label, type: m.type, isPast: m.isPast });
    });
  });
  return dates;
}
function monthCardHTML(ym, entries, isCurrent, colorMap, isNext){
  const [y, m] = ym.split('-');
  return `
    <div class="month-card${isCurrent ? ' current-month' : (isNext ? ' next-month' : '')}">
      <div class="month-head">${MONTH_NAMES[parseInt(m,10)-1]} ${y}${isCurrent ? '<span class="now-badge">NOW</span>' : (isNext ? '<span class="now-badge next-badge">NEXT</span>' : '')}</div>
      <div class="month-entries">
        ${entries.map(e => {
          const c = colorMap.get(e.venueId) || hashColor(e.venueId);
          return `
          <div class="month-entry${e.isPast ? ' month-entry-past' : ''}" style="background:${c.bg};border-left-color:${c.border};cursor:pointer;" onclick="goToTrackerCard('${e.venueId}')" title="${e.isPast ? 'This date has passed — likely a stale/un-refreshed cycle. ' : ''}Jump to ${e.label}">
            <span class="day" style="color:${c.text};">${parseInt(e.date.slice(8,10),10)}</span>
            <span class="entry-text" style="color:${c.text};">
              <strong style="color:${c.text};">${e.label}</strong> — ${e.text}
              <span class="entry-type">${e.type}${e.isPast ? ' · passed' : ''}</span>
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
  const nextYM = new Date(now.getFullYear(), now.getMonth()+1, 1).toISOString().slice(0,7);

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
    const monthsHTML = months.map(ym => monthCardHTML(ym, byMonth[ym].sort((a,b) => a.date.localeCompare(b.date)), ym === currentYM, colorMap, ym === nextYM)).join('');
    return `
      <div class="calendar-swimlane">
        <div class="swimlane-head text-xs font-bold uppercase tracking-wide text-slate-500 mb-2">${lane.label}</div>
        <div class="flex gap-4 overflow-x-auto pb-2 calendar-strip">${monthsHTML}</div>
      </div>
    `;
  }).join('');

  // Months are laid out oldest-first (left to right) so users can scroll left/back
  // to see past months, but the default viewport should open on the current month
  // rather than the oldest one — so scroll each strip forward to the current-month
  // card right after paint. Falls back to leaving scroll at 0 (leftmost = oldest)
  // when a lane has no current-month card, e.g. all its dates are in the past.
  requestAnimationFrame(() => {
    container.querySelectorAll('.calendar-strip').forEach(strip => {
      const currentCard = strip.querySelector('.month-card.current-month');
      if(currentCard) strip.scrollLeft = currentCard.offsetLeft;
    });
  });
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
  const bars = progressBarHTML({ not_started: s.not_started, in_progress: s.in_progress, completed: s.completed }, s.total, PROGRESS_STATUS_LABEL, ['in_progress', 'not_started']);
  document.getElementById('homeProgressTrack').innerHTML = bars.track;
  document.getElementById('homeProgressLegend').innerHTML = bars.legend;

  const ctaEl = document.getElementById('homeTrackCTA');
  if(ctaEl){
    ctaEl.innerHTML = s.total === 0
      ? `<button class="pop-btn font-bold px-6 py-3 text-white" style="background-color: #f79256; border: none; cursor: pointer; border-radius: 999px;" onclick="showPage('wizard')">Find your first opportunity to track</button>`
      : `<button class="pop-btn bg-white font-bold text-xs px-4 py-2 rounded-xl" style="color: #1d4e89;" onclick="showPage('wizard')">Look for Fresh Finds</button>`;
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
          <p class="font-bold text-sm truncate" style="color: #1d4e89;">${item.name}</p>
          <p class="text-xs" style="color: #4A6685;">${shortDate(nextDate)} · ${nextLabel}${taskCount ? ` · ${taskCount} task${taskCount > 1 ? 's' : ''}` : ''}</p>
        </div>
        ${statusPillHTML(status)}
      </div>
    `;
  }).join('');
  listEl.insertAdjacentHTML('beforeend', `<div class="text-left pt-2"><button class="pop-btn bg-white font-bold text-xs px-4 py-2 rounded-xl" style="color: #1d4e89;" onclick="event.stopPropagation(); openTodoModal();">See all tasks</button></div>`);
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
        // Also update beyond section if it's visible
        const beyondSection = document.getElementById('homeTodoBeyondSection');
        if(beyondSection && !beyondSection.classList.contains('hidden')){
          renderHomeTodoBeyond();
        }
      }
      return;
    }
  }
}
function renderTodoModalContent(){
  const wrap = document.getElementById('todoModalBody');
  if(!wrap) return;
  const upcoming = getUpcomingDeadlineItems();
  const beyond = getBeyondDeadlineItems();
  const allTasks = [...upcoming, ...beyond];

  if(!allTasks.length){
    wrap.innerHTML = `<p class="empty-state">Nothing due — you're all caught up.</p>`;
    return;
  }

  // Helper function to render a single task card
  const renderTaskCard = ({ item, nextDate, nextLabel }) => {
    const status = computeProgressStatus(item);
    const actionRows = (item.actionItems || []).map(ai => `
      <div class="flex items-center justify-between gap-3 py-1.5">
        <span class="text-xs font-medium text-slate-700 ${ai.state === 'completed' ? 'line-through text-slate-400' : ''}">${ai.text}${ai.url ? ` <a href="${ai.url}" target="_blank" class="text-indigo-600 hover:underline" title="Go to this step" onclick="event.stopPropagation();">↗</a>` : ''}</span>
        <button class="status-pill status-task-${ai.state} cursor-pointer" onclick="cycleActionItemState('${item.id}','${ai.id}')" title="Click to change status">${ACTION_ITEM_STATUS_LABEL[ai.state]}</button>
      </div>
    `).join('');
    return `
      <div class="bg-slate-50 border-2 border-slate-200 rounded-2xl p-4 mb-3">
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
  };

  let html = '';

  // Render upcoming and beyond tasks together without section headers
  html = allTasks.map(renderTaskCard).join('');

  wrap.innerHTML = html;
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

// ---------- Beyond next month tasks ----------
function getBeyondDeadlineItems(){
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
      if(key > nextMonthKey){
        if(ensureActionItems(item)) backfilled = true;
        results.push({ item, bucket, nextDate: next.date, nextLabel: next.label, nextKind: next.kind });
      }
    });
  });
  if(backfilled) saveTrackerData();
  results.sort((a, b) => {
    const statusDiff = TRACKER_STATUS_ORDER[computeProgressStatus(a.item)] - TRACKER_STATUS_ORDER[computeProgressStatus(b.item)];
    if(statusDiff !== 0) return statusDiff;
    return a.nextDate.localeCompare(b.nextDate);
  });
  return results;
}

function renderHomeTodoBeyond(){
  const listEl = document.getElementById('homeTodoBeyondList');
  const trackEl = document.getElementById('todoBeyondProgressTrack');
  if(!listEl || !trackEl) return;
  const upcoming = getBeyondDeadlineItems();
  const { counts, total } = allTodoUnitCounts(upcoming);
  const bars = progressBarHTML(counts, total, ACTION_ITEM_STATUS_LABEL, ['not_started', 'in_progress', 'completed'], 'task');
  trackEl.innerHTML = bars.track;
  const statCountsEl = document.getElementById('todoBeyondStatCounts');
  if(statCountsEl){
    const statOrder = ['not_started', 'in_progress', 'completed'];
    statCountsEl.innerHTML = statOrder.map(k => `<span class="status-pill status-task-${k}">${counts[k]} ${ACTION_ITEM_STATUS_LABEL[k]}</span>`).join('');
  }

  if(!upcoming.length){
    listEl.innerHTML = `<p class="empty-state">Nothing due beyond next month.</p>`;
    return;
  }
  listEl.innerHTML = upcoming.map(({ item, nextDate, nextLabel }) => {
    const status = computeProgressStatus(item);
    const taskCount = (item.actionItems || []).length;
    return `
      <div class="flex items-center justify-between gap-3 py-2 border-b border-slate-100 last:border-0">
        <div class="min-w-0">
          <p class="font-bold text-sm truncate" style="color: #1d4e89;">${item.name}</p>
          <p class="text-xs" style="color: #4A6685;">${shortDate(nextDate)} · ${nextLabel}${taskCount ? ` · ${taskCount} task${taskCount > 1 ? 's' : ''}` : ''}</p>
        </div>
        ${statusPillHTML(status)}
      </div>
    `;
  }).join('');
  listEl.insertAdjacentHTML('beforeend', `<div class="text-left pt-2"><button class="pop-btn bg-white font-bold text-xs px-4 py-2 rounded-xl" style="color: #1d4e89;" onclick="event.stopPropagation(); openTodoModal();">See all tasks</button></div>`);
}

function showTodoBeyond(){
  const beyondSection = document.getElementById('homeTodoBeyondSection');
  const ctaWrap = document.getElementById('todoBeyondCtaWrap');
  if(beyondSection && ctaWrap){
    beyondSection.classList.remove('hidden');
    ctaWrap.classList.add('hidden');
    renderHomeTodoBeyond();
  }
}

function hideTodoBeyond(){
  const beyondSection = document.getElementById('homeTodoBeyondSection');
  const ctaWrap = document.getElementById('todoBeyondCtaWrap');
  if(beyondSection && ctaWrap){
    beyondSection.classList.add('hidden');
    ctaWrap.classList.remove('hidden');
  }
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
      <div class="urgent-pulse text-white p-6 flex flex-wrap items-center justify-between gap-4" style="background: linear-gradient(135deg, #00b2ca, #1d4e89); border-radius: 22px; box-shadow: 0 2px 18px rgba(15, 23, 42, 0.06);">
        <div>
          <p class="font-heading font-extrabold text-lg">Your profile is empty</p>
          <p class="text-sm font-medium opacity-90 mt-1 max-w-md">Every match in the Finder gets better once we know you. Takes 2 minutes — go build it now.</p>
        </div>
        <button class="pop-btn font-bold px-6 py-3 shrink-0" style="background-color: #fff; color: #1d4e89; border: none; cursor: pointer; border-radius: 999px;" onclick="goToProfile()">Build my profile</button>
      </div>
    `;
    return;
  }

  if(isStale){
    wrap.innerHTML = `
      <div class="urgent-pulse p-6 flex flex-wrap items-center justify-between gap-4" style="background-color: #FCE9D0; border-radius: 22px; box-shadow: 0 2px 18px rgba(15, 23, 42, 0.06);">
        <div>
          <p class="font-heading font-extrabold text-lg" style="color: #8A4A0E;">⏰ Your profile is ${days} days old</p>
          <p class="text-sm font-medium mt-1 max-w-md" style="color: #8A4A0E;">Stale profiles mean stale matches — a quick refresh keeps your suggestions sharp.</p>
        </div>
        <button class="pop-btn font-bold px-4 py-2.5 rounded-xl text-sm shrink-0" style="background-color: #f79256; color: #fff;" onclick="goToProfile()">Update my profile →</button>
      </div>
    `;
    return;
  }

  wrap.innerHTML = `
    <div class="bg-white p-6 space-y-4" style="border-radius: 22px; box-shadow: 0 2px 18px rgba(15, 23, 42, 0.06);">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 class="font-heading font-bold text-xl">Your Story So Far</h2>
        <button class="pop-btn font-bold px-4 py-2.5 rounded-xl text-sm shrink-0" style="background-color: #f79256; color: #fff;" onclick="goToProfile()">View &amp; deepen it →</button>
      </div>
      <p class="text-sm font-medium line-clamp-3" style="color: #4A6685;">${escapeHtmlTracker(studentProfile.synthesized)}</p>
    </div>
  `;
}

// ---------- Profile tab: single synthesized profile (read-only summary) ----------
// Static by design — the summary itself is never directly editable. The only action
// available here is clearing it completely; every add/update/correction happens through
// the "Deepen your story" drawer, which is the sole source of truth for what gets merged
// into this summary.

// Sentences that appeared in the profile only after the last merge. Rendered wrapped in
// <mark> so the student can see what the chat actually added, then dropped after 5s.
let profileHighlightSet = null;
let profileHighlightTimer = null;
// Set alongside the highlight set and consumed by the next render, so the card scrolls to
// the new text exactly once — the 5s expiry re-render and every unrelated renderProfileFit
// call must not yank the page around again.
let profileScrollToHighlight = false;

// What the student said in the session currently being folded in. Closing the drawer kicks
// off two AI round-trips (distil the transcript, then re-synthesize the whole profile) that
// take several seconds together, and a card that sits unchanged for that long reads as
// "closing it threw my answers away". So their own words go up on the card immediately and
// are replaced by the synthesized version when it lands — see mergeIntoProfile, which
// clears this at the exact moment the merged text is rendered.
let profilePendingText = null;

function splitProfileSentences(text){
  return (text || '').split(/(?<=[.!?])\s+/).map(s => s.trim()).filter(Boolean);
}

// How much word overlap makes a sentence a reworded version of one already in the profile
// rather than a new one. Synthesis rewrites the whole profile every merge, so it routinely
// returns settled content with a word or two changed ("into marine biology" ->
// "interested in marine biology"); exact-match diffing lights all of that up and the
// highlight stops meaning "here is what you just added". Measured separation on real
// merges is wide — a reworded sentence scores ~0.8, genuinely new content ~0.1 — so this
// sits between them rather than near either.
const PROFILE_REWORD_RATIO = 0.6;

function sentenceWords(text){
  return new Set(text.toLowerCase().match(/[a-z0-9']+/g) || []);
}

// Jaccard overlap, so a long sentence can't score high against a short one merely by
// containing it — an appended clause should still read as new.
function sentenceSimilarity(a, b){
  const A = sentenceWords(a), B = sentenceWords(b);
  if(!A.size || !B.size) return 0;
  let shared = 0;
  A.forEach(w => { if(B.has(w)) shared++; });
  return shared / (A.size + B.size - shared);
}

// Diffs by sentence rather than by whole paragraph: a merge commonly appends a clause to
// an existing paragraph, and highlighting the entire paragraph would drown the one new bit.
function flagNewProfileText(before, after){
  const old = splitProfileSentences(before);
  const oldExact = new Set(old);
  const added = splitProfileSentences(after).filter(s =>
    !oldExact.has(s) && !old.some(o => sentenceSimilarity(s, o) >= PROFILE_REWORD_RATIO)
  );
  if(profileHighlightTimer) clearTimeout(profileHighlightTimer);
  if(!added.length){
    profileHighlightSet = null;
    return;
  }
  profileHighlightSet = new Set(added);
  profileScrollToHighlight = true;
  profileHighlightTimer = setTimeout(() => {
    profileHighlightSet = null;
    profileHighlightTimer = null;
    renderProfileFit();
  }, 5000);
}

function profileTextHTML(text){
  if(!profileHighlightSet || !profileHighlightSet.size) return escapeHtmlTracker(text);
  return splitProfileSentences(text).map(s =>
    profileHighlightSet.has(s) ? `<mark class="profile-new">${escapeHtmlTracker(s)}</mark>` : escapeHtmlTracker(s)
  ).join(' ');
}

function vibeField(label, innerHTML){
  return `<div class="vibe-field"><p class="vibe-label">${label}</p>${innerHTML}</div>`;
}

// The basics tiles are derived from the profile text (see the `basics` slot in
// PROFILE_DERIVED_SLOTS), so they cost one cached AI call per meaningful profile change
// rather than one per render. Anything the student never mentioned reads "No info".
// Latches when the extraction call fails, so the tiles settle on "No info" instead of
// spinning forever — and so a failing key isn't retried on every single re-render. Cleared
// whenever the profile text changes, which is a fresh chance for the call to succeed.
let profileBasicsUnavailable = false;

function renderProfileBasics(){
  const wrap = document.getElementById('profileBasicsGrid');
  if(!wrap) return;
  const text = studentProfile.synthesized || '';
  if(!text){
    wrap.innerHTML = '';
    return;
  }
  const fresh = profileDerivedIsFresh('basics', studentProfile.basics, text);
  const fields = (studentProfile.basics && studentProfile.basics.fields) || {};
  wrap.innerHTML = PROFILE_BASICS_FIELDS.map(({ key, label }) => {
    const value = fields[key];
    const cls = value ? 'vibe-value' : 'vibe-value empty';
    const shown = value ? profileTextHTML(value) : ((fresh || profileBasicsUnavailable) ? 'No info' : '…');
    return vibeField(label, `<p class="${cls}">${shown}</p>`);
  }).join('');
  if(!fresh && !profileBasicsUnavailable){
    getProfileDerived('basics')
      .then(renderProfileBasics)
      .catch(err => {
        console.warn('Profile basics extraction failed:', err.message);
        profileBasicsUnavailable = true;
        renderProfileBasics();
      });
  }
}

function renderProfileFit(){
  const contentWrap = document.getElementById('profileContent');
  const ctaBanner = document.getElementById('profileCtaBanner');
  const insufficientBanner = document.getElementById('profileInsufficientBanner');

  if(!contentWrap) return;

  renderProfileBasics();

  if(!studentProfile.synthesized){
    contentWrap.innerHTML = `
      <p class="empty-state">Nothing here yet — chat with the bot to build your profile.</p>
      <button class="mt-6 pop-btn font-bold px-6 py-3 text-white" style="background-color: #f79256; border-color: #1a2540; cursor: pointer; border-radius: 999px;" onclick="openStoryDrawer()">Start chatting</button>
    `;
    if(ctaBanner) ctaBanner.classList.add('hidden');
    if(insufficientBanner) insufficientBanner.classList.add('hidden');
    return;
  }

  const allParagraphs = studentProfile.synthesized.split(/\n\s*\n/).map(p => p.trim()).filter(Boolean);
  const passionProjects = [];
  const researchProjects = [];
  const generalParagraphs = [];
  allParagraphs.forEach(p => {
    if(/^passion projects?:/i.test(p)) passionProjects.push(p.replace(/^passion projects?:\s*/i, ''));
    else if(/^research projects?:/i.test(p)) researchProjects.push(p.replace(/^research projects?:\s*/i, ''));
    else generalParagraphs.push(p);
  });

  const numbered = items => `<ol class="vibe-list">${items.map(p => `<li>${profileTextHTML(p)}</li>`).join('')}</ol>`;

  let html = '';
  if(generalParagraphs.length){
    html += vibeField('Interests &amp; experience', generalParagraphs.map(p =>
      `<p class="vibe-value vibe-body">${profileTextHTML(p)}</p>`
    ).join(''));
  }
  if(passionProjects.length) html += vibeField('Passion projects', numbered(passionProjects));
  if(researchProjects.length) html += vibeField('Research projects', numbered(researchProjects));
  if(profilePendingText){
    html += `<div class="vibe-field vibe-pending">
      <p class="vibe-label">Just shared — folding this in<span class="vibe-dots"></span></p>
      <p class="vibe-value vibe-body">${escapeHtmlTracker(profilePendingText)}</p>
    </div>`;
  }
  contentWrap.innerHTML = html;

  const isSufficient = countProfileWords(studentProfile.synthesized) >= PROFILE_SUFFICIENT_LENGTH;
  if(ctaBanner) ctaBanner.classList.toggle('hidden', !isSufficient);
  if(insufficientBanner) insufficientBanner.classList.toggle('hidden', isSufficient);

  if(profileScrollToHighlight){
    profileScrollToHighlight = false;
    scrollToFirstProfileHighlight();
  }
}

// Brings the freshly-merged text into view once synthesis lands. The wait between closing
// the drawer and the merged profile appearing is several seconds, which is long enough for
// the student to have scrolled elsewhere on the page — landing them on the new sentence is
// the point of highlighting it at all. Deliberately silent when My Vibe isn't the visible
// page: showPage() scrolls to top on arrival, and a background merge yanking a page the
// student is reading is worse than no scroll.
function scrollToFirstProfileHighlight(){
  const page = document.getElementById('page-profile');
  if(!page || page.classList.contains('hidden')) return;
  // Synchronous, not deferred to requestAnimationFrame: the mark is already in the DOM
  // (innerHTML was assigned just above) so there is nothing to wait for, and rAF does not
  // run at all in a backgrounded tab — the callback would sit queued until the student
  // came back to the tab and then jump the page under them, seconds after the fact.
  const mark = document.querySelector('#profileContent mark.profile-new');
  if(mark) mark.scrollIntoView({ behavior: 'smooth', block: 'center' });
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
    item.actionItems = GENERIC_ACTION_ITEMS.map((text, i) => ({ id: `${item.id}-gt${i}`, text, url: null, state: 'not_started' }));
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
  document.getElementById('activeDrawerCount').textContent = String(sortedItems.length).padStart(2, '0');

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

// ---------- Export/sync tracker deadlines to Google Calendar ----------
// Shared by the .ics download (exportAllDeadlinesToGoogle) and the live API sync
// (syncToGoogleCalendar) below, so the two never disagree about which deadlines count.
// Only actively-tracked items (not saved-for-later) with a real dateISO are included.
function collectTrackedDeadlineEvents(){
  const allItems = [];
  ALL_BUCKETS.forEach(bucket => {
    trackerData[bucket].forEach(item => {
      if(!trackerSavedState[item.id]){
        allItems.push(item);
      }
    });
  });

  const events = [];
  allItems.forEach(item => {
    (item.importantDates || []).forEach((date, idx) => {
      // Handle both dateISO and date_iso (underscore version)
      const dateValue = date.dateISO || date.date_iso;
      if(dateValue){
        events.push({
          itemId: item.id,
          dateIdx: idx,
          name: item.name,
          org: item.org,
          dateISO: dateValue,
          dateLabel: date.label || 'Deadline',
          url: item.url,
          googleEventId: date.googleEventId || null
        });
      }
    });
  });
  return events;
}

function exportAllDeadlinesToGoogle(){
  const events = collectTrackedDeadlineEvents();

  if(!events.length){
    alert('No tracked deadlines to export.');
    return;
  }

  // Generate iCalendar (.ics) file content
  const icsContent = generateICS(events);

  // Trigger download
  const blob = new Blob([icsContent], { type: 'text/calendar' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `wingman-deadlines-${new Date().toISOString().slice(0,10)}.ics`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// Kicks off the calendar-connect OAuth flow — see handle_google_calendar_start in
// server.py. Separate grant from Google Sign-In (that only ever asks for openid/email/
// profile), so this is needed even for an account that already signed in with Google.
function connectGoogleCalendar(){
  if(!currentUser || !currentUser.userid){
    alert('Please sign in first.');
    return;
  }
  location.href = `/api/auth/google/calendar/start?userid=${encodeURIComponent(currentUser.userid)}`;
}

// Pushes every tracked deadline to the signed-in user's primary Google Calendar via
// /api/calendar/sync (handle_calendar_sync in server.py). Re-running this updates
// previously-synced events in place rather than duplicating them: each successfully
// synced date gets a googleEventId written back onto trackerData and persisted, and
// that id is sent along on the next sync so the server PATCHes instead of inserting.
async function syncToGoogleCalendar(){
  if(!currentUser || !currentUser.userid){
    alert('Please sign in first.');
    return;
  }
  const events = collectTrackedDeadlineEvents();
  if(!events.length){
    alert('No tracked deadlines to sync.');
    return;
  }

  const btn = document.getElementById('syncCalendarBtn');
  if(btn){ btn.disabled = true; btn.textContent = '⏳ Syncing...'; }

  try{
    const res = await fetch('/api/calendar/sync', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        userid: currentUser.userid,
        events: events.map(e => ({
          id: `${e.itemId}::${e.dateIdx}`,
          title: e.org ? `${e.name} (${e.org})` : e.name,
          description: e.url ? `${e.dateLabel}\nURL: ${e.url}` : e.dateLabel,
          dateISO: e.dateISO,
          googleEventId: e.googleEventId
        }))
      })
    });
    const data = await res.json().catch(() => ({}));

    if(res.status === 409){
      if(confirm('Google Calendar isn\'t connected yet. Connect it now?')){
        connectGoogleCalendar();
      }
      return;
    }
    if(!res.ok){
      alert(data.error || 'Could not sync to Google Calendar.');
      return;
    }

    // Write each synced event's googleEventId back onto trackerData so a future sync
    // updates it in place instead of creating a duplicate, then persist.
    let okCount = 0, errCount = 0;
    const byKey = {};
    (data.results || []).forEach(r => { byKey[r.id] = r; });
    ALL_BUCKETS.forEach(bucket => {
      trackerData[bucket].forEach(item => {
        (item.importantDates || []).forEach((date, idx) => {
          const r = byKey[`${item.id}::${idx}`];
          if(!r) return;
          if(r.status === 'ok'){ date.googleEventId = r.googleEventId; okCount++; }
          else { errCount++; }
        });
      });
    });
    await saveTrackerData();

    alert(errCount
      ? `Synced ${okCount} deadline${okCount === 1 ? '' : 's'} to Google Calendar (${errCount} failed).`
      : `Synced ${okCount} deadline${okCount === 1 ? '' : 's'} to Google Calendar.`);
  }catch(e){
    alert('Could not reach the server to sync to Google Calendar.');
  }finally{
    if(btn){ btn.disabled = false; btn.textContent = '🔄 Sync to Google Calendar'; }
  }
}

// Generate iCalendar format (.ics) content from events
function generateICS(events){
  // iCalendar format: https://en.wikipedia.org/wiki/ICalendar
  const now = new Date().toISOString().replace(/[-:]/g, '').split('.')[0] + 'Z';
  const icsLines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Highschool Wingman//Calendar Export//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'X-WR-CALNAME:Wingman Deadlines',
    'X-WR-TIMEZONE:UTC'
  ];

  events.forEach((event, idx) => {
    const dateISO = event.dateISO; // YYYY-MM-DD format
    const startDate = dateISO.replace(/-/g, ''); // YYYYMMDD

    // Calculate end date (all-day events in iCalendar end on the day after)
    const [year, month, day] = dateISO.split('-');
    const endDateObj = new Date(year, parseInt(month) - 1, parseInt(day) + 1);
    const endDateStr = endDateObj.getFullYear().toString() +
      String(endDateObj.getMonth() + 1).padStart(2, '0') +
      String(endDateObj.getDate()).padStart(2, '0');

    // Create a unique ID for the event (RFC 5545 requires unique UIDs)
    const uid = `wingman-${dateISO}-${event.name.replace(/[^a-z0-9]/gi, '')}-${idx}@wingman`;

    const summary = event.org ? `${event.name} (${event.org})` : event.name;
    const description = event.url ? `URL: ${event.url}` : '';

    // Build event lines
    const eventLines = [
      'BEGIN:VEVENT',
      `UID:${uid}`,
      `DTSTAMP:${now}`,
      `DTSTART;VALUE=DATE:${startDate}`,
      `DTEND;VALUE=DATE:${endDateStr}`,
      `SUMMARY:${escapeICS(summary)} - ${escapeICS(event.dateLabel)}`
    ];

    if(description){
      eventLines.push(`DESCRIPTION:${escapeICS(description)}`);
    }

    eventLines.push('END:VEVENT');
    icsLines.push(...eventLines);
  });

  icsLines.push('END:VCALENDAR');
  return icsLines.join('\r\n');
}

// Helper: escape special characters in iCalendar format (RFC 5545)
function escapeICS(text){
  if(!text) return '';
  return text
    .replace(/\\/g, '\\\\')      // Backslash must be escaped first
    .replace(/;/g, '\\;')        // Semicolon
    .replace(/,/g, '\\,')        // Comma
    .replace(/\n/g, '\\n')       // Newline
    .replace(/\r/g, '\\n');      // Carriage return
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
        // Overlay the shared/cached, on-demand deadline check (server-side, cross-user,
        // 7-day TTL — see server.py's /api/opportunities/<id>/deadline) on top of
        // extractTrackerInfo()'s own guess. This is the trigger point the caching design
        // is built around: adding an opportunity to the tracker is what causes the first
        // real check (or reuses another user's still-fresh cached result) for it.
        applyDeadlineCheckToInfo(info, await fetchDeadlineCheck(opp.id));
        trackerData[bucket].push({
          id: opp.id,
          name: opp.name,
          url: opp.url,
          type: opp.type,
          bucket: bucket,
          progressStatus: 'not_started',
          status: ['running','not_running','unknown'].includes(info.status) ? info.status : 'unknown',
          reviewStatus: opp.review_status || null,
          reviewSummary: opp.review_summary || null,
          meta: info.meta || [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
          fit: info.fit || opp.summary,
          note: info.note || 'Details from the opportunities database — confirm on the official site.',
          noteType: info.status === 'not_running' ? 'flag' : (info.noteType || 'plain'),
          importantDates: Array.isArray(info.important_dates)
            ? info.important_dates.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Date', dateISO: d.date_iso, type: d.type || 'deadline' })).sort((a, b) => a.dateISO.localeCompare(b.dateISO))
            : [],
          deadlineLabel: info.deadline_label || 'CHECK SITE',
          wasEstimated: !!info.was_estimated,
          requirements: Array.isArray(info.requirements) ? info.requirements.slice(0, 5) : null,
          applyUrl: info.apply_url || opp.url,
          applyLabel: info.apply_label || 'Apply / learn more',
          actionItems: Array.isArray(info.action_items)
            ? info.action_items.slice(0, 5).map((ai, i) => ({
                id: `${opp.id}-t${i}`,
                text: typeof ai === 'string' ? ai : ai.text,
                url: (typeof ai === 'object' && ai.url) ? ai.url : null,
                state: 'not_started'
              }))
            : []
        });
        newlyAddedTrackerIds.add(opp.id);
      }catch(err){
        console.error(`Failed to fetch details for ${opp.name}:`, err);
        trackerData[bucket].push({
          id: opp.id, name: opp.name, url: opp.url, type: opp.type,
          bucket: bucket, progressStatus: 'not_started',
          status: 'unknown',
          reviewStatus: opp.review_status || null,
          reviewSummary: opp.review_summary || null,
          meta: [opp.org, opp.type, opp.price, opp.location].filter(Boolean).join(' · '),
          fit: opp.summary,
          note: 'Live details couldn\'t be fetched — showing database info only. Check the official site directly.',
          noteType: 'flag',
          importantDates: [], deadlineLabel: 'CHECK SITE', wasEstimated: false,
          requirements: null, applyUrl: opp.url, applyLabel: 'Visit site', actionItems: []
        });
        newlyAddedTrackerIds.add(opp.id);
      }
    }
  }

  await saveTrackerData();

  if(typeof firebase !== 'undefined' && firebase.analytics) {
    firebase.analytics().logEvent('tracker_items_added', {
      'item_count': newlyAddedTrackerIds.size,
      'total_tracked': Object.values(trackerData).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0),
      'bucket_count': buckets.length
    });
  }

  btn.disabled = false;
  btn.classList.remove('loading');
  label.textContent = 'Add to my tracker →';

  // Re-render the results list now, while still on the Finder, so each newly-tracked
  // card's "Save Match" button flips to the "In Quest Log" tag immediately. showPage('wizard')
  // just toggles stage visibility without re-rendering (see goStage), so without this the
  // stale "Save Match" buttons would still show if the user navigates back here later.
  selectedIds = new Set();
  renderResults();

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
      // Overlay the shared/cached on-demand deadline check on top — same server endpoint
      // buildTracker() uses. It internally no-ops into a cheap cache hit if this item was
      // checked (by any user) within the last 7 days, and only runs a fresh web_search
      // when actually stale, so "check for updates" no longer forces a live search for
      // every tracked item on every click.
      applyDeadlineCheckToInfo(info, await fetchDeadlineCheck(item.id));
      const oldStatus = item.status;
      const oldImportantDatesKey = JSON.stringify(item.importantDates);
      item.status = ['running','not_running','unknown'].includes(info.status) ? info.status : item.status;
      item.meta = info.meta || item.meta;
      item.fit = info.fit || item.fit;
      item.note = info.note || item.note;
      item.noteType = item.status === 'not_running' ? 'flag' : (info.noteType || item.noteType);
      if(Array.isArray(info.important_dates)){
        item.importantDates = info.important_dates.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Date', dateISO: d.date_iso, type: d.type || 'deadline' })).sort((a, b) => a.dateISO.localeCompare(b.dateISO));
      }
      item.wasEstimated = !!info.was_estimated;
      if(Array.isArray(info.requirements)) item.requirements = info.requirements.slice(0, 5);
      item.applyUrl = info.apply_url || item.applyUrl;
      item.applyLabel = info.apply_label || item.applyLabel;
      if(oldStatus !== item.status || JSON.stringify(item.importantDates) !== oldImportantDatesKey){
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
  const thisYear = new Date().getFullYear();
  const nextYear = thisYear + 1;
  const system = `You classify and extract structured tracking data for a student extracurricular opportunity from a URL, for a high-school tracker. Today's date is ${today}.

First determine 'section': 'conferences' for academic conferences/workshops that review and present papers, 'journals' for academic/student journals with manuscript submission, 'researchCompetitions' for science fairs, app challenges, and project/research-based contests where a project or paper is submitted and judged, 'pureCompetitions' for skills/knowledge tests with no project submitted (olympiads, quiz competitions, exams), 'internships' for hands-on mentored work positions with a lab, company, or organization, 'summerPrograms' for camps, enrichment programs, or coursework.

Search thoroughly with web_search, in order: (1) the given URL; (2) "site:${root} ${nextYear}" / "site:${root} ${thisYear}" for a current/upcoming-cycle page (orgs often publish a year-specific page separate from the evergreen landing page); (3) ALWAYS ALSO search the most recent PAST cycle (e.g. "site:${root} ${thisYear} deadline" and the year before that, computed from ${today}) even if step 2 succeeded — this is your mandatory estimation basis; (4) "site:${root} FAQ"/"key dates"/"timeline" for the base site if still stale or missing. Look for language indicating the program is discontinued/not running this cycle — set status to "not_running" if so, and don't estimate dates for it.

Estimation is expected and encouraged, not a last resort — apply in order: (a) explicit current/upcoming-cycle dates found → use them, was_estimated:false; (b) no current-cycle dates but real prior-cycle dates found and the program looks recurring → roll each forward ~1 year, was_estimated:true, status:"running" (the expected path when a new cycle's page isn't live yet — don't default to "unknown"); (c) only a vague pattern found (e.g. "opens in fall") → construct a concrete estimated date from it, was_estimated:true, explain briefly in note; (d) genuinely nothing current or prior-cycle found after trying step 3 → status:"unknown" (should be rare).

Find EVERY pertinent date — registration opens, early-bird vs. regular deadline, notification date, and event/conference start-end dates — each with a short label and a "type" of "opens", "deadline", "event_start", "event_end", or "other", in chronological order. Pay particular, deliberate attention to the registration/application OPENS date, not just the deadline — this is the field most often missed. Only omit a date category if there's genuinely no basis to find or estimate one (per step d above). Every date you have enough basis to mention in "note" (e.g. "registration typically opens Sept") must ALSO appear as a matching "important_dates" entry (was_estimated:true) — never describe date info in "note" without a corresponding structured entry, and vice versa. Prefer including a reasonably-estimated date over omitting it.

Also think through 3-5 short, concrete action items a student would need to do to meet the nearest deadline (e.g. request a recommendation letter, draft an essay, gather transcripts) — infer these from requirements and what's typical for this type of opportunity. Keep every item tactical and administrative — the logistics of applying, never advice about the student's own project or its substance, since you don't know the specifics of their work and must not assume or invent any. Skip if status is not_running.
For each action item, also give your best-guess direct URL for where the student would actually go to do it — the specific application/submission portal, payment or fee page, account sign-up/registration page, or test-registration page, as applicable. Use the most specific URL you found during search (not just the homepage) whenever one exists; reuse the general apply/info URL if nothing more specific applies; use null only if you genuinely found no plausible page — never invent a URL path that wasn't actually seen.

Respond with ONLY a raw JSON object, no markdown fences, no preamble, no text after the JSON: {"name":"program/opportunity name from the page, or organization name if no program name found, under 50 chars","section":"conferences, journals, researchCompetitions, pureCompetitions, internships, or summerPrograms","status":"running, not_running, or unknown","meta":"one short line: dates/location/fee/format","fit":"one sentence, under 25 words","note":"one sentence, under 25 words","noteType":"good, plain, or flag","important_dates":[{"label":"short label","date_iso":"YYYY-MM-DD","type":"opens, deadline, event_start, event_end, or other"}],"deadline_label":"short text like ROLLING, only if important_dates is empty","was_estimated":true or false,"requirements":[{"date":"...","text":"under 12 words"}],"apply_url":"...","apply_label":"short button label","category":"short type label like 'Science fair' or 'Rationality camp', or null","action_items":[{"text":"short concrete task, under 10 words","url":"best-guess direct URL for this specific action, or null"}]}. Stay well within 1000 tokens: at most 4 important_dates, 3 requirements, and 5 action_items.`;
  const userContent = `URL: ${url}\n${notes ? `Extra context: ${notes}\n` : ''}\nFetch this URL, classify it, and extract tracking details per the schema.`;
  return callGeminiJSON(system, userContent, true);
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
      name: extracted.name || 'Custom Opportunity',
      url,
      type: extracted.category || '',
      bucket: bucket,
      progressStatus: 'not_started',
      status: ['running','not_running','unknown'].includes(extracted.status) ? extracted.status : 'unknown',
      meta: extracted.meta || '',
      fit: extracted.fit || '',
      note: extracted.note || 'Added manually via URL.',
      noteType: extracted.status === 'not_running' ? 'flag' : (extracted.noteType || 'plain'),
      importantDates: Array.isArray(extracted.important_dates)
        ? extracted.important_dates.filter(d => d && d.date_iso).map(d => ({ label: d.label || 'Date', dateISO: d.date_iso, type: d.type || 'deadline' })).sort((a, b) => a.dateISO.localeCompare(b.dateISO))
        : [],
      deadlineLabel: extracted.deadline_label || 'CHECK SITE',
      wasEstimated: !!extracted.was_estimated,
      requirements: Array.isArray(extracted.requirements) ? extracted.requirements.slice(0, 5) : null,
      applyUrl: extracted.apply_url || url,
      applyLabel: extracted.apply_label || 'Apply / learn more',
      actionItems: Array.isArray(extracted.action_items)
        ? extracted.action_items.slice(0, 5).map((ai, i) => ({
            id: `${id}-t${i}`,
            text: typeof ai === 'string' ? ai : ai.text,
            url: (typeof ai === 'object' && ai.url) ? ai.url : null,
            state: 'not_started'
          }))
        : []
    };
    trackerData[bucket].push(item);
    await saveTrackerData();
    renderTrackerPage();

    status.textContent = `Added "${item.name}" ✓`;
    urlInput.value = '';
    notesInput.value = '';
    goToTrackerCard(id);

    // Submit to opportunities database in background (non-blocking)
    submitUserOpportunityToDatabase(extracted, url, bucket);

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

// ---------- Submit user opportunity to database in background ----------
function submitUserOpportunityToDatabase(extracted, url, bucket){
  // Fire and forget — submit extracted data to /api/user-submitted-opportunities
  // without blocking the UI. Runs in background.
  (async () => {
    try {
      const payload = {
        name: extracted.name || 'Custom Opportunity',
        url: url,
        type: extracted.category || 'Program',
        section: bucket,
        meta: extracted.meta || '',
        fit: extracted.fit || '',
        note: extracted.note || '',
        important_dates: extracted.important_dates || [],
        requirements: extracted.requirements || [],
        apply_url: extracted.apply_url || url,
        category: extracted.category || null,
        // Provenance for the admin review queue — a reviewer judging whether a submitted
        // row is real wants to know who sent it. Signed-out submissions omit it and show
        // up unattributed, the same residual the cost attribution reports.
        userid: (currentUser && currentUser.userid) || null
      };
      const res = await fetch('/api/user-submitted-opportunities', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if(!res.ok) {
        const err = await res.json().catch(() => ({ message: 'Unknown error' }));
        console.warn('Failed to submit opportunity to database:', err);
      } else {
        const result = await res.json();
        console.log('Opportunity queued for database:', result);
      }
    } catch(err) {
      console.warn('Error submitting opportunity to database:', err);
      // Silent fail — the opportunity is already in the user's tracker, database submission
      // is just a background nicety. Don't annoy the user if it fails.
    }
  })();
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
// A fresh Google redirect takes precedence over whatever loadUser() finds cached —
// see handleGoogleRedirect(), which handles both the sign-in and finish-signup cases
// and returns true whenever it did.
const calendarJustConnected = checkCalendarConnectedRedirect();
handleGoogleRedirect().then((handled) => {
  if(handled) return;
  loadUser().then(() => {
    if(currentUser){
      showApp().then(() => {
        if(calendarJustConnected) alert('Google Calendar connected! Use "Sync to Google Calendar" in your Quest Log to push your tracked deadlines.');
      });
    } else {
      showLandingPage();
      if(calendarJustConnected) alert('Google Calendar was connected, but you were signed out in the process — please sign in again, then sync from your Quest Log.');
    }
  });
});