import sys

def replace_all(filename):
    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. renderKindGrid
    old_kind = """  grid.innerHTML = ordered.map(key => {
    const c = KIND_CONFIG[key];
    if(c.comingSoon){
      return `
        <div class="kind-card disabled">
          <div class="kind-name"><span>${c.name} <span class="info-icon" title="${c.desc}">ⓘ</span></span><span class="kind-source coming-soon">Coming soon</span></div>
        </div>
      `;
    }
    return `
      <button class="kind-card" onclick="selectKind('${key}')">
        <div class="kind-name"><span>${c.name} <span class="info-icon" title="${c.desc}">ⓘ</span></span></div>
      </button>
    `;
  }).join('');"""
    new_kind = """  grid.innerHTML = ordered.map(key => {
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
  }).join('');"""
    content = content.replace(old_kind, new_kind)

    # 2. renderSuggestEntryCard
    old_suggest = """function renderSuggestEntryCard(){
  const el = document.getElementById('suggestEntryCard');
  if(!el) return;
  if(!studentProfile.synthesized){
    el.classList.add('disabled');
    el.innerHTML = `
      <div class="entry-choice-title">Suggest opportunities for me</div>
      <p class="entry-choice-desc">Based on everything in your profile. Add a few things to your profile first and this option unlocks.</p>
      <button class="secondary-btn" onclick="showPage('home')">Go to Your Profile →</button>
    `;
    return;
  }
  el.classList.remove('disabled');
  const preview = studentProfile.synthesized.length > 110 ? studentProfile.synthesized.slice(0, 110) + '…' : studentProfile.synthesized;
  el.innerHTML = `
    <div class="entry-choice-title">Suggest opportunities for me</div>
    <p class="entry-choice-desc">Skip straight to matches based on your profile.</p>
    <p class="entry-choice-meta" style="font-style:italic;">"${escapeHtmlTracker(preview)}"</p>
    <button class="primary-btn" onclick="startProfileSuggest()">Suggest opportunities for me →</button>
  `;
}"""
    new_suggest = """function renderSuggestEntryCard(){
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
}"""
    content = content.replace(old_suggest, new_suggest)

    # 3. resultCardHTML
    old_result = """function resultCardHTML(r){
  const o = r.opp;
  const isSelected = selectedIds.has(o.id);
  const metaParts = [o.org, o.type, o.price, o.location, o.state && o.state !== 'All States' ? o.state : null, o.season].filter(Boolean);
  const kindBadge = r.kind ? `<span class="type-badge">${KIND_CONFIG[r.kind] ? KIND_CONFIG[r.kind].name : r.kind}</span>` : '';
  return `
    <div class="result-card${isSelected ? ' selected' : ''}" id="result-${o.id}">
      <div class="card-main">
        <h3 class="result-title"><a href="${o.url}" target="_blank" rel="noopener">${o.name}</a>${kindBadge}</h3>
        <div class="result-meta">
          <span class="tier-badge ${TIER_CLASS[r.tier]}">${TIER_LABEL[r.tier]}</span>
          <span>${metaParts.join(' · ')}</span>
        </div>
        ${r.reason ? `<p class="result-reason">${r.reason}</p>` : ''}
        ${o.summary ? `<p class="expandable" onclick="this.classList.toggle('expanded')">${o.summary}<span class="expand-toggle"></span></p>` : ''}
      </div>
      <button class="select-toggle${isSelected ? ' selected' : ''}" onclick="toggleSelect('${o.id}')">${isSelected ? '✓ Selected' : 'Select'}</button>
    </div>
  `;
}"""
    new_result = """function resultCardHTML(r){
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
}"""
    content = content.replace(old_result, new_result)

    # 4. trackerCardHTML
    old_tracker = """function trackerCardHTML(item, sourceLabel){
  const b = trackerBadge(item);
  const notRunningBadge = item.status === 'not_running'
    ? `<span class="type-badge" style="color:var(--redline);border-color:var(--redline);background:var(--redline-soft);">Not currently running</span>`
    : '';
  const sourceBadge = sourceLabel ? `<span class="type-badge" style="border-style:dashed;">${sourceLabel}</span>` : '';
  const estimatedNote = item.wasEstimated && item.status !== 'not_running'
    ? `<div class="card-alt-note">These dates are a prediction based on a past cycle — the official next-cycle dates aren't posted yet. Confirm on the site before relying on them.</div>`
    : '';
  const deadlineRows = (item.deadlines && item.deadlines.length)
    ? `<div class="req-title">Deadlines</div>
       <ul class="req-list">
         ${item.deadlines.map(d => `<li><span class="req-date">${shortDate(d.dateISO)}</span>${d.label}</li>`).join('')}
       </ul>`
    : '';
  const isSaved = !!trackerSavedState[item.id];
  const saveBtn = `<button class="save-btn${isSaved ? ' saved' : ''}" onclick="event.stopPropagation(); toggleTrackerSaved('${item.id}')" title="${isSaved ? 'Move back to its section' : 'Save for later'}">${isSaved ? '★ Saved — click to restore' : '☆ Save for later'}</button>`;
  const deleteBtn = `<button class="delete-btn" onclick="event.stopPropagation(); deleteTrackerItem('${item.id}', this)" title="Remove permanently">🗑 Delete</button>`;
  const progress = item.progressStatus || 'not_started';
  const progressSelector = `
    <div class="progress-selector">
      <button class="progress-opt${progress === 'not_started' ? ' active' : ''}" onclick="event.stopPropagation(); setProgressStatus('${item.id}','not_started')">Not Started</button>
      <button class="progress-opt${progress === 'in_progress' ? ' active' : ''}" onclick="event.stopPropagation(); setProgressStatus('${item.id}','in_progress')">In Progress</button>
      <button class="progress-opt${progress === 'completed' ? ' active' : ''}" onclick="event.stopPropagation(); setProgressStatus('${item.id}','completed')">Completed</button>
    </div>
  `;
  const detailsBody = `
    <p class="card-fit">${item.fit}</p>
    ${item.requirements ? `
    <div class="req-title">Other requirements</div>
    <ul class="req-list">
      ${item.requirements.map(r => `<li><span class="req-date">${r.date}</span>${r.text}</li>`).join('')}
    </ul>` : ''}
  `;
  return `
    <div class="card${item.status === 'not_running' ? ' card-not-running' : ''}" id="tracker-card-${item.id}">
      <div class="card-main">
        <h3 class="card-title"><a href="${item.url}" target="_blank" rel="noopener">${item.name}</a>${item.type ? `<span class="type-badge">${item.type}</span>` : ''}${notRunningBadge}${sourceBadge}</h3>
        <div class="card-meta">${item.meta}</div>
        ${progressSelector}
        ${estimatedNote}
        ${deadlineRows}
        <details class="card-details">
          <summary>Show details</summary>
          <div class="card-details-body">${detailsBody}</div>
        </details>
        <p class="card-notes ${item.noteType}">${item.note}</p>
        <div>
          <a class="submit-link" href="${item.applyUrl}" target="_blank" rel="noopener">${item.applyLabel}</a>
          ${saveBtn}
          ${deleteBtn}
        </div>
      </div>
      <div class="stamp-badge ${b.cls}">${b.top}<span class="days">${b.bottom}</span></div>
    </div>
  `;
}"""
    new_tracker = """function trackerCardHTML(item, sourceLabel){
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
};"""
    content = content.replace(old_tracker, new_tracker)

    # 5. renderProfileFit
    old_profile = """function renderProfileFit(){
  const wrap = document.getElementById('profileFitSection');
  if(!wrap) return;

  if(editingProfile){
    wrap.innerHTML = `
      <div class="profile-fit-card">
        <textarea class="profile-entry-edit-box" id="profileEditBox" rows="4" placeholder="Add anything new — a project, interest, research topic, or an update to something already here…"></textarea>
        <div class="profile-entry-edit-actions">
          <button class="secondary-btn" id="profileEditSaveBtn" onclick="saveProfileEdit()">Save</button>
          <button class="secondary-btn" onclick="cancelProfileEdit()">Cancel</button>
        </div>
        <p class="form-status" id="profileEditStatus"></p>
      </div>
    `;
    return;
  }

  if(!studentProfile.synthesized){
    wrap.innerHTML = `
      <div class="profile-fit-card">
        <p class="empty-state" style="padding:14px;margin:0 0 12px;">Nothing yet — describe yourself in the Finder, or add something here.</p>
        <button class="secondary-btn" onclick="startProfileEdit()">+ Add to profile</button>
      </div>
    `;
    return;
  }

  wrap.innerHTML = `
    <div class="profile-fit-card">
      <p class="profile-entry-text">${escapeHtmlTracker(studentProfile.synthesized)}</p>
      <div class="profile-entry-actions">
        <button class="entry-action-btn" onclick="startProfileEdit()">Edit</button>
        <button class="entry-action-btn" onclick="clearProfile(this)">Delete</button>
      </div>
    </div>
  `;
}"""
    new_profile = """function renderProfileFit(){
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
}"""
    content = content.replace(old_profile, new_profile)

    with open(filename, 'w', encoding='utf-8') as f:
        f.write(content)

replace_all('script.js')
print("Replaced script elements")
