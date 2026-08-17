with open('script.js', 'r', encoding='utf-8') as f:
    content = f.read()

bad_showpage = """function showPage(page){
  ['home','wizard','tracker'].forEach(p => {
    const el = document.getElementById('page-' + p);
    if(el) {
      if(p === page) {
        el.classList.remove('hidden');
      } else {
        el.classList.add('hidden');
      }
      el.style.display = '';
    }
  });

  const activeColors = { home: 'bg-yellow-300', wizard: 'bg-lime-300', tracker: 'bg-indigo-300' };

  ['home','wizard','tracker'].forEach(p => {
    const btnId = 'nav' + p.charAt(0).toUpperCase() + p.slice(1) + 'Btn';
    const btn = document.getElementById(btnId);
    if(btn) {
      if(p === page) {
        btn.classList.remove('bg-white');
        btn.classList.add(activeColors[p]);
      } else {
        btn.classList.add('bg-white');
        btn.classList.remove('bg-yellow-300', 'bg-lime-300', 'bg-indigo-300');
      }
    }
  });

  if(page === 'home') renderHomePage();
  if(page === 'tracker') renderTrackerPage();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}"""

content = content.replace(bad_showpage, '')

old_showpage = """function showPage(name){
  document.getElementById('page-home').style.display = name === 'home' ? '' : 'none';
  document.getElementById('page-wizard').style.display = name === 'wizard' ? '' : 'none';
  document.getElementById('page-tracker').style.display = name === 'tracker' ? '' : 'none';
  document.getElementById('navHomeBtn').classList.toggle('active', name === 'home');
  document.getElementById('navWizardBtn').classList.toggle('active', name === 'wizard');
  document.getElementById('navTrackerBtn').classList.toggle('active', name === 'tracker');
  if(name === 'tracker'){ renderTrackerPage(); }
  if(name === 'home'){ renderHomePage(); }
  if(name === 'wizard'){ renderSuggestEntryCard(); }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}"""

new_showpage = """function showPage(name){
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
}"""

content = content.replace(old_showpage, new_showpage)

with open('script.js', 'w', encoding='utf-8') as f:
    f.write(content)
