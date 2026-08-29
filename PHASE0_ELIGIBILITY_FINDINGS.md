# Phase 0 Data Spike — Eligibility / Grade / Subject-Tags / Location

_Read-only pull of `opportunities` where `is_active=true`. Total active rows: **1488**. Rows with non-empty `eligibility`: **1073** (72.1%)._

## 1. Eligibility parse-quality

Keyword-heuristic bucket hit-counts (a row can hit several):

| Bucket | Rows | % of eligibility rows |
|---|---:|---:|
| grade_age | 1032 | 96.2% |
| citizenship | 87 | 8.1% |
| geographic | 192 | 17.9% |
| demographic | 86 | 8.0% |
| prereq | 114 | 10.6% |

Buckets-hit-per-row distribution:

| # buckets hit | rows |
|---:|---:|
| 0 | 23 |
| 1 | 675 |
| 2 | 296 |
| 3 | 72 |
| 4 | 7 |

Top bucket pair overlaps:

| pair | rows |
|---|---:|
| geographic + grade_age | 192 |
| grade_age + prereq | 111 |
| citizenship + grade_age | 76 |
| demographic + grade_age | 75 |
| demographic + geographic | 27 |
| citizenship + prereq | 25 |
| geographic + prereq | 17 |
| citizenship + demographic | 13 |
| citizenship + geographic | 13 |
| demographic + prereq | 5 |

### Hard-vs-soft demographic (n=86 demographic-mentioning rows)

| class | count |
|---|---:|
| hard | 6 |
| hard_scope | 16 |
| soft | 31 |
| ambiguous | 0 |
| unclear | 33 |
| **hard total** (hard + hard_scope) | **22** |

- **hard** = explicit exclusionary keyword (only / must / required / restricted to), no soft cue
- **hard_scope** = demographic term is the DEFINING population (e.g. "female-identifying and non-binary students", "BIPOC students") with no soft/inclusive cue — exclusionary by scope though it never says "only"
- **soft** = encouragement/priority keyword present (particularly / especially / typically / encouraged / priority given), no hard keyword
- **ambiguous** = BOTH a hard and a soft cue present (needs human eyes)
- **unclear** = demographic term but no hard, soft, or scope cue matched

### Verbatim samples — HARD (6 total, showing up to 40)

- `ec18818` **Youth Mental Health Ambassadorship** — High school students passionate about Asian community and/or mental health advocacy, and interested in related education or career paths. Must be able to attend biweekly Saturday virtual group sessions and monthly mentor meetings.
- `ec18760` **Business Emerging Leaders (BEL) Scholarship Program** — Must be a current high school senior applying for direct admission to the undergraduate business program at UW–Madison for Fall enrollment; must identify with specific criteria such as leadership, first-generation status, or coming from underserved communities.
- `ec18820` **Fermilab Program for Research, Innovation, and STEM Mentorship** — {"gender": null, "nationality": ["US Citizen"], "residency": null, "income": null, "other_requirements": "Must be a High School Senior for the 2027-2028 school year or a 2027 high school graduate."}
- `ec18826` **YoungArts National Competition: Visual Arts** — Must be a US citizen, permanent resident, or legally able to receive taxable income in the US, and must not have previously attended National YoungArts Week as an award winner with distinction.
- `ec18825` **YoungArts National Competition: Photography** — Must be a US citizen, permanent resident, or legally able to receive taxable income in the US, and must not have previously attended National YoungArts Week as a winner with distinction.
- `ec18824` **YoungArts National Competition: Design Arts** — Must be a U.S. citizen, permanent resident, or legally able to receive taxable income in the U.S. If selected as an award winner with distinction, it must be the applicant's first time attending National YoungArts Week.

### Verbatim samples — HARD_SCOPE (16 total, showing up to 40)

- `ec18132` **Pathways for Women in Business Leadership** — Rising high school sophomore, junior, and senior female students.
- `ec18281` **Aspirations in Computing Award (AiC)** — Open to US-based young women, genderqueer, and non-binary students in grades 9-12.
- `ec18324` **Creative Career Cohort** — BIPOC students aged 15-19
- `ec17907` **Girls in Math at Yale** — Female-identifying and non-binary high school students
- `ec17612` **Girls Talk Math Camp (UMD)** — Open to high school students who are girls or non-binary.
- `ec17630` **Native Education Forum** — Native American or Indigenous rising high school juniors and seniors
- `ec17213` **Sibling Circles** — Open to young women and gender-expansive youth.
- `ec17258` **NYU GSTEM** — Rising high school juniors and seniors (female and gender-expansive students)
- `ec17119` **Summer Camp LBL (SAGE LBL)** — Open to high school students in Northern California, specifically focusing on girls and gender-diverse students interested in STEM.
- `ec17703` **Engineering Discovery for Girls** — Open to girls entering middle school or high school depending on specific camp tracks.
- `ec18813` **Mental Health Ambassador Program** — High school students ages 14 to 18 and young adults ages 18 to 27 from Black and Brown communities.
- `ec17212` **Siblinghood Academy** — Open to young women and gender-expansive youth.
- `ec17214` **Leadership Scholars** — Open to high school students (Nashers) identifying as women or non-binary.
- `ec17756` **Health and Society Institute** — Open to rising high school juniors and seniors (female-identifying).
- `ec17211` **Leadership Project (Summer Institute)** — Open to young women and gender-expansive youth in high school.
- `ec17215` **Nash U (Sadie Nash Leadership Project)** — High school seniors (female-identifying and gender-expansive youth)

### Verbatim samples — SOFT (31 total, showing up to 40)

- `ec18000` **Young Researchers Program (USC)** — High school students from underrepresented or underserved backgrounds in the Los Angeles area.
- `ec18084` **Summer Promise Program** — Rising 9th-grade students, particularly from underrepresented communities
- `ec18154` **Future Success Program** — High school students eligible for TRIO programs (typically first-generation, low-income, or with academic need)
- `ec17490` **CURE - Summer Only (Harvard)** — Massachusetts high school and college students from underrepresented populations in biomedical research.
- `ec17458` **Pomona College Academy for Youth Success (PAYS)** — Rising sophomores through rising seniors who are first-generation and/or from low-income families, typically serving local students underrepresented in higher education.
- `ec18177` **STEP Summer Program** — New York State secondary students (grades 7-12, varying by specific criteria such as economically disadvantaged or historically underrepresented minorities in STEM)
- `ec18321` **Fresh Perspectives Youth Curatorial Residency** — Youth ages 15–17; prior cohorts prioritized BIPOC young adults
- `ec18409` **Pipeline Dreams: High School** — High school juniors and seniors from underrepresented backgrounds in medicine (URiM, low-income, first-gen).
- `ec18068` **PREFACE: The RPI Summer Engineering Design Program** — Rising high school juniors and seniors from historically underrepresented groups in engineering and technological fields.
- `ec17768` **RBS-PREP Pre-College Enrichment** — High school juniors from underrepresented and underserved backgrounds.
- `ec18365` **Johns Hopkins Internship in Brain Sciences - Virtual Program** — High school juniors and seniors, specifically targeting students from underrepresented backgrounds.
- `ec17696` **ARISE UWCCC Summer High School Cancer Research** — High school students from diverse backgrounds underrepresented in biomedical fields.
- `ec17613` **WIE Rise Summer Research** — High school students, typically grades 6-12 (specifically focusing on women/gender minorities in engineering depending on WIE guidelines)
- `ec18588` **MITES Summer** — High school juniors who are U.S. citizens or permanent residents; underrepresented, low-income, or first-generation students strongly encouraged
- `ec17147` **Project Success** — High school students in Boston and Cambridge, particularly those underrepresented in medicine and/or from disadvantaged backgrounds.
- `ec17829` **Summer Humanities and Social Science Program (Amherst)** — Incoming first-year college students who are from underrepresented minority groups and/or first-generation college students.
- `ec18319` **YouthCAN** — High school students ages 13–19; open to all art experience levels; AAPI and underrepresented communities especially encouraged
- `ec18165` **Junior Doctors of Tomorrow** — High school students (typically rising juniors and seniors), with an emphasis on students from disadvantaged or underrepresented backgrounds.
- `ec18812` **Teen Mental Health Ambassadors** — Current high school students (9th-12th grades) who live or go to school in Lake County, IN (Gary) or Essex County, NJ (Newark). All genders and backgrounds welcomed; no prior experience needed.
- `ec18889` **Stanford Program for Inspiring the Next Generation of Women in Physics** — High school students in 9th through 11th grade at the time of application. Open to international applicants. Students of any gender are welcome to apply.
- `ec18636` **Girls Who Code Pathways & Summer Immersion Program (SIP)** — Rising 9th-12th graders (and international students aged 14-18) identifying as female, non-binary, or gender-nonconforming. Beginners welcome.
- `ec18063` **M-ASCEND** — Undergraduate students, particularly underrepresented minorities interested in medicine and health disparities.
- `ec17644` **EnvironMentors (Penn State University)** — High school students, particularly underrepresented and underserved populations in the local area.
- `ec18408` **MERIT Health Leadership Academy** — Baltimore City high school sophomores from underrepresented backgrounds with an unweighted GPA of 2.75+.
- `ec18530` **Princeton AI4ALL Summer Program** — 10th graders at the time of application residing in the US; priority given to students with financial need, prospective first-generation college students, and groups underrepresented in STEM.
- `ec17331` **STRIDE Bioengineering (UCR)** — High school students from Riverside and San Bernardino counties (Inland Empire region) from diverse or underrepresented backgrounds.
- `ec17330` **HealthLink** — First-generation college-bound high school students in the Bay Area, with high school sophomores given preference.
- `ec17401` **Princeton Summer Journalism Program** — Current high school juniors in the US/Puerto Rico with a minimum 3.5 unweighted GPA from limited-income backgrounds (family income typically under $65,000 or qualifying for free/reduced lunch/fee waivers).
- `ec17464` **Summer Academy (Meta)** — Open to high school students, typically focusing on underrepresented students in tech through the Foundation For A College Education.
- `ec17435` **State Pre-College Enrichment Program (S-PREP)** — 7th - 12th grade students interested in medicine or related STEM fields, particularly underrepresented and economically disadvantaged students.
- `ec18744` **Youth Mental Health Academy (YMHA)** — Rising 11th and 12th graders in California (recruitment focused on Los Angeles County) from underrepresented backgrounds

### Verbatim samples — AMBIGUOUS (0 total, showing up to 20)


### Verbatim samples — UNCLEAR (33 total, showing up to 20)

- `ec18585` **YoungArts National Foundation Competition (Classical & Jazz Music Disciplines)** — High school students in grades 10–12 or ages 15–18; US citizens, permanent residents, or individuals legally able to receive taxable income in the US.
- `ec18087` **Crimson Summer Academy** — High school students from economically underserved backgrounds, primarily local to the Boston/Cambridge area.
- `ec18676` **Chicago Summer Business Institute (CSBI)** — High school sophomores and juniors who reside in the City of Chicago, maintain a 3.0+ GPA, and have a household income under $80,000.
- `ec17324` **CURE Internship** — High school juniors underrepresented in health careers
- `ec17524` **Upward Bound (UPENN)** — Students at specific Philadelphia high schools who demonstrate financial need or will be first-generation-to-college students.
- `ec17485` **Biomedical Research Internship** — Graduating high school seniors and college freshmen, traditionally underrepresented in biomedical careers.
- `ec18326` **YoungArts National Competition** — U.S. citizens, permanent residents/green card holders, or those able to receive taxable income in the U.S., in grades 10–12 or ages 15–18.
- `ec17547` **Dentistry Saturday Academy** — High school students, with a focus on underrepresented students interested in healthcare and dentistry.
- `ec18315` **Seattle Youth Employment Program (SYEP) – Career Pathways** — Ages 16–24; must live within Seattle city limits or attend Seattle Public Schools; household income at or below 80% Area Median Income
- `ec17371` **Health Professions Recruitment & Exposure Program (HPREP)** — High school students of diverse backgrounds from the Chicagoland area.
- `ec18563` **Humanity's Kitchen High School Culinary Program** — High school students (ages 14–22, grades 9–12) in Delaware, including accommodations for students with disabilities.
- `ec17491` **YES for CURE** — High school and undergraduate students interested in biomedical research, specifically targeting underrepresented populations.
- `ec18751` **YoungArts National Arts Competition (Theater Discipline)** — U.S. citizens, permanent residents, or legally able to receive taxable income in the U.S.; enrolled in grades 10–12 or ages 15–18 on December 1
- `ec17785` **ASPIRE Scholars Program (UNLV)** — Students at selected Title I middle and high schools in Nevada.
- `ec17778` **TRIO Upward Bound Math & Science (UNLV)** — First-generation and income-qualified high school students from select target schools
- `ec17830` **STEM Incubator** — First-generation or low-income undergraduate students without previous STEM research experience.
- `ec17437` **Double Discovery Center** — First-generation, low-income students from target neighborhoods (Harlem and Washington Heights) attending local target schools in grades 9-12.
- `ec18836` **Project Promise Health Careers Summer Camp** — {"min_age": null, "max_age": null, "gender": null, "residency": "Rural Western North Carolina (specific counties listed: Madison, Yancey, Mitchell, McDowell, Rutherford, Polk, Henderson, Transylvania, Cherokee, Clay, Swain, Jackson, Graham, Macon, Haywood)", "target_demographics": ["Rural students"], "gpa_min": 3.0, "other_requirements": ["Strong desire to pursue a career in healthcare"]}
- `ec18578` **YoungArts National Arts Competition – Film Discipline** — US citizens, permanent residents, or individuals legally able to receive taxable income in the US, in grades 10–12 or ages 15–18.
- `ec17523` **Upward Bound Math Science** — High school students from low-income backgrounds and/or potential first-generation college students

## 2. Grade-capture rate

- grade_min set: **1019** / 1488 (68.5%)
- grade_max set: **1051** / 1488 (70.6%)
- either set: **1051** / 1488 (70.6%)
- both set: **1019** / 1488 (68.5%)
- **Extractor-miss** (eligibility mentions grade/age but BOTH grade cols NULL): **38** rows

Extractor-miss sample (up to 30):

- `ec18941` **Southwest Teen Life Center Youth Programs** — Youth age 13-19
- `ec18557` **Regional Dance America (RDA) Dance & Choreography Intensive** — High school and pre-professional dancers/choreographers (audition or regional member affiliation required).
- `ec18576` **School of Doc** — High school students enrolled in Durham Public Schools.
- `ec18155` **Quest Project** — Middle school students (typically grades 6-8, not eligible for high school students)
- `ec18709` **The Junior Academy** — Students aged 13 to 17 worldwide
- `ec18579` **NYU Tisch Summer High School Filmmakers Workshop** — High school students who are at least 15 years old at the start of the program.
- `ec17699` **Precollege Academic Campus Experience (PACE)** — Students finishing grades 5-8
- `ec17609` **Camp Shule (UMD)** — Open to youth/school-aged children (typically elementary to middle school, though ages vary; check specific site details).
- `ec18554` **Idyllwild Arts Summer Dance Intensive** — Ages 12–17; video audition required for new applicants.
- `ec18577` **Teen Producers Project** — Youth ages 13–18 (12–18 for Intro). Advanced level requires prior production experience.
- `ec18575` **Youth Documentary Academy (YDA)** — Youth ages 14–20 residing in Colorado Springs and surrounding areas.
- `ec18573` **Fresh Films Weekly Filmmaking Program** — Teens ages 13–19. No prior experience required.
- `ec18984` **Journal of Research High School (JRHS)** — High school researchers
- `ec18527` **Summer@Cornish Art & Design Intensive** — High school arts students typically ages 14–18.
- `ec18574` **Youth Documentary Workshop (YDW)** — Public high school students in New York City (especially Transfer, International, Consortium, and District 79 schools).
- `ec18593` **NASA Office of STEM Engagement (OSTEM) High School Internship** — High school students at least 16 years old at the time of application, U.S. citizens, with a minimum 3.0 cumulative GPA on a 4.0 scale.
- `ec18594` **Children's National Hospital - Mentored Experience to Expand Opportunities in Research (METEOR v3)** — High school students residing in the Washington, D.C. metropolitan area.
- `ec18776` **Immerse Education Fashion & Design Summer School** — Open to participants aged 15–18 with an interest in visual design and fashion; supports ESL learners.
- `ec18777` **TED Immerse Education Summer Courses in New York** — 15-18 years old
- `ec18779` **Fashion & Sewing Summer Camp for Teens in NYC** — Ages 13-17
- `ec18638` **Atlas Fellowship** — High school students aged 13 to 19 globally (including gap year students who have not started university).
- `ec18647` **Oxford Scholastica Academy Summer School** — Students aged 12–18 internationally (age bands 12–14 and 15–18)
- `ec18809` **NYU Shanghai AI Pre-College Summer Program** — High school students aged 15–18
- `ec18813` **Mental Health Ambassador Program** — High school students ages 14 to 18 and young adults ages 18 to 27 from Black and Brown communities.
- `ec18843` **Roc Nation Summer Academy** — High School Students Ages 15-17
- `ec17872` **3M Young Scientist Challenge** — Open to students in grades 5-8 in the United States.
- `ec18850` **GROWTH Internship Program** — Must be 16 to 19 years old and reside in Benton Harbor or Niles, attend a Benton Harbor or Niles area high school, or attend a school of choice while living in Benton Harbor or Niles.
- `ec18857` **Youth Conservation Crews** — Must be North Carolina residents and at least 15 years old at the start of the crew (ages 15-18).
- `ec18861` **Green Schools Alliance Internship** — Age 15+, fluent in English, and proficient writers
- `ec18901` **SGMC Health Student Shadowing Program** — High school and college students

## 3. Subject-tags vocabulary breadth

- Rows with non-empty subject_tags: **1487** / 1488 (99.9%)
- Distinct tag values: **929**

Shape samples (type, value):

- (list) ["Mixed", "Leadership"]
- (list) ["STEM", "Engineering", "Computer Science"]
- (list) ["Humanities", "Leadership"]
- (list) ["Humanities", "Education", "Leadership"]
- (list) ["STEM", "Biology"]

Full vocabulary by frequency (top 60):

| tag | count |
|---|---:|
| STEM | 662 |
| Biology | 268 |
| Humanities | 263 |
| Engineering | 254 |
| Medicine | 251 |
| Computer Science | 230 |
| Mixed | 224 |
| Leadership | 213 |
| Art | 158 |
| Business | 147 |
| Mathematics | 134 |
| Physics | 104 |
| Chemistry | 83 |
| Research | 71 |
| Healthcare | 45 |
| Science | 41 |
| Education | 31 |
| Law | 28 |
| Environmental Science | 26 |
| Artificial Intelligence | 26 |
| Design | 25 |
| Entrepreneurship | 25 |
| Astronomy | 24 |
| Data Science | 22 |
| Summer Program | 20 |
| Competition | 20 |
| Architecture | 19 |
| Arts | 19 |
| Mentorship | 18 |
| Internship | 18 |
| Technology | 18 |
| Machine Learning | 17 |
| Public Policy | 16 |
| Psychology | 16 |
| leadership | 15 |
| Music | 15 |
| Robotics | 15 |
| Community Service | 15 |
| College Prep | 15 |
| Academics | 15 |
| Career Exploration | 14 |
| Public Speaking | 13 |
| Writing | 13 |
| Cybersecurity | 13 |
| Neuroscience | 13 |
| Programming | 12 |
| Journalism | 12 |
| Public Health | 12 |
| Marketing | 12 |
| Performing Arts | 12 |
| Sustainability | 12 |
| Cancer Research | 11 |
| Mental Health | 11 |
| Coding | 11 |
| Scientific Research | 10 |
| Data Analysis | 10 |
| Logic | 10 |
| Pre-College | 10 |
| Civic Engagement | 10 |
| Social Sciences | 10 |

_…and 869 more distinct tags (long tail)._

## 4. Location shape

`location` value distribution:

| location | rows |
|---|---:|
| In-Person | 870 |
| In-Person and Remote | 220 |
| <null> | 204 |
| Remote | 194 |

- `state` populated: **1284** / 1488 (86.3%)
