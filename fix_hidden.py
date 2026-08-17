with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix elements that have Tailwind 'hidden' class AND are toggled by JS via 'show' class
# These must start hidden via our own CSS class, not Tailwind's hidden
replacements = [
    # suggestError
    ('class="hidden mt-4 bg-rose-100 text-rose-900 p-4 rounded-xl border-2 border-rose-900 font-bold text-sm form-error" id="suggestError"',
     'class="mt-4 bg-rose-100 text-rose-900 p-4 rounded-xl border-2 border-rose-900 font-bold text-sm form-error" id="suggestError"'),
    # progressNote
    ('class="hidden mt-4 text-xs font-bold text-indigo-600 progress-note" id="progressNote"',
     'class="mt-4 text-xs font-bold text-indigo-600 progress-note" id="progressNote"'),
    # formError
    ('class="hidden mt-4 bg-rose-100 text-rose-900 p-4 rounded-xl border-2 border-rose-900 font-bold text-sm form-error" id="formError"',
     'class="mt-4 bg-rose-100 text-rose-900 p-4 rounded-xl border-2 border-rose-900 font-bold text-sm form-error" id="formError"'),
    # trackerIntakeError
    ('class="hidden mt-2 bg-rose-100 text-rose-900 p-2 rounded-lg border-2 border-rose-900 font-bold text-xs intake-error" id="trackerIntakeError"',
     'class="mt-2 bg-rose-100 text-rose-900 p-2 rounded-lg border-2 border-rose-900 font-bold text-xs intake-error" id="trackerIntakeError"'),
    # trackerChangeBanner
    ('class="hidden bg-emerald-100 border-2 border-emerald-900 text-emerald-900 p-4 rounded-xl font-bold text-sm change-banner" id="trackerChangeBanner"',
     'class="bg-emerald-100 border-2 border-emerald-900 text-emerald-900 p-4 rounded-xl font-bold text-sm change-banner" id="trackerChangeBanner"'),
    # trackerErrorBanner  
    ('class="hidden bg-rose-100 border-2 border-rose-900 text-rose-900 p-4 rounded-xl font-bold text-sm error-banner" id="trackerErrorBanner"',
     'class="bg-rose-100 border-2 border-rose-900 text-rose-900 p-4 rounded-xl font-bold text-sm error-banner" id="trackerErrorBanner"'),
]

for old, new in replacements:
    if old in content:
        content = content.replace(old, new)
        print(f'Fixed: ...{new[:60]}...')
    else:
        print(f'NOT FOUND: ...{old[:60]}...')

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done!")
