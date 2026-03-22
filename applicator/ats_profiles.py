"""
ATS (Applicant Tracking System) portal profiles for browser automation.

Each profile contains:
- Detection logic (URL patterns, DOM markers)
- Application flow steps
- Account requirements
- Tricky UI elements for Playwright
- Form field patterns and selectors

Research compiled March 2026.
"""

ATS_PROFILES = {

    # =========================================================================
    # 1. WORKDAY / myWorkdayJobs
    # =========================================================================
    "workday": {
        "name": "Workday",
        "aliases": ["myWorkdayJobs"],

        "detection": {
            "url_patterns": [
                r"myworkdayjobs\.com",
                r"\.wd\d+\.myworkdayjobs\.com",   # e.g. company.wd5.myworkdayjobs.com
                r"workday\.com/.*careers",
                r"myworkday\.com",
                r"myworkdaysite\.com",             # Workday variant (same engine)
            ],
            "dom_markers": [
                "[data-automation-id]",                         # Workday's primary attribute
                "[data-automation-id='jobPostingHeader']",
                "[data-automation-id='navigationWidget']",
            ],
        },

        "account_required": True,
        "account_notes": (
            "One account PER COMPANY. Each employer's Workday instance requires "
            "separate sign-up with email + password. Returning applicants can reuse "
            "credentials for the same company. 'Use My Last Application' option may "
            "appear for repeat applicants to pre-fill data."
        ),

        "application_flow": [
            "1. Land on job posting page (company.wd5.myworkdayjobs.com/...)",
            "2. Click 'Apply' button  [data-automation-id='jobPostingApplyButton'] or similar",
            "3. Sign In / Create Account page appears",
            "   - Create Account: email, password (some require name)",
            "   - Sign In: email + password",
            "   - 'Use My Last Application' checkbox may appear",
            "4. Resume upload page - upload PDF/DOCX, Workday parses it",
            "5. 'My Information' page - contact info, address, phone, links",
            "6. 'My Experience' page - work history, education (may pre-fill from resume)",
            "7. 'Application Questions' page - custom screening questions per job",
            "8. 'Voluntary Disclosures' page - EEO, veteran, disability (optional)",
            "9. 'Self-Identify' page - demographics (optional)",
            "10. Review & Submit page",
        ],

        "resume_upload": {
            "method": "File input or drag-drop zone",
            "accepted_formats": ["PDF", "DOCX", "DOC"],
            "selector_hints": [
                "[data-automation-id='resumeDropzone']",
                "[data-automation-id='file-upload-input-ref']",
                "input[type='file']",
            ],
            "notes": "Workday parses the resume and pre-fills subsequent pages. Wait 3-5s after upload for parsing.",
        },

        "tricky_ui": [
            "data-automation-id attributes are the ONLY reliable selectors; class names are obfuscated/hashed",
            "Dropdown menus are custom Workday widgets, NOT native <select> elements. Click to open, then click option.",
            "Multi-step form: each page loads dynamically (SPA), URL may not change between steps",
            "Form pages use 'Next' / 'Continue' buttons: [data-automation-id='bottom-navigation-next-button']",
            "Date fields may use custom date-pickers, not standard <input type='date'>",
            "Address fields often have autocomplete popups that must be selected",
            "Overlays/modals may block clicks - use JS click to bypass: element.click() via page.evaluate()",
            "Session timeout is aggressive (~15-20 min idle); keep interactions flowing",
            "No shadow DOM, but heavy use of aria attributes and custom data attributes",
        ],

        "field_selectors": {
            "pattern": "data-automation-id based",
            "examples": {
                "email": "[data-automation-id='email']",
                "password": "[data-automation-id='password']",
                "first_name": "[data-automation-id='legalNameSection_firstName']",
                "last_name": "[data-automation-id='legalNameSection_lastName']",
                "phone": "[data-automation-id='phone-number']",
                "next_button": "[data-automation-id='bottom-navigation-next-button']",
                "submit_button": "[data-automation-id='submit-button']",
            },
            "notes": "Selectors vary slightly between Workday versions. Fall back to text content matching.",
        },
    },

    # =========================================================================
    # 2. LEVER
    # =========================================================================
    "lever": {
        "name": "Lever",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"jobs\.lever\.co/",
                r"lever\.co/.*/apply",
                r"api\.lever\.co/",
                r"api\.eu\.lever\.co/",   # EU instance
            ],
            "dom_markers": [
                ".posting-page",
                ".posting-headline",
                ".application-page",
                "form.application-form",
                "[data-qa='posting-name']",
            ],
        },

        "account_required": False,
        "account_notes": "No account needed. Single-page application form. Some employers add 'Apply with LinkedIn' button.",

        "application_flow": [
            "1. Land on posting page: jobs.lever.co/{company}/{posting-id}",
            "2. Click 'Apply for this job' to scroll to / navigate to apply form",
            "   OR direct URL: jobs.lever.co/{company}/{posting-id}/apply",
            "3. Single-page form with all fields visible:",
            "   - Full Name (required)",
            "   - Email (required)",
            "   - Phone (optional by default)",
            "   - Current Company (optional)",
            "   - Resume/CV upload",
            "   - Cover Letter (text area or file upload, depends on config)",
            "   - LinkedIn URL, GitHub URL, Portfolio URL",
            "   - Additional Information (free text)",
            "   - Custom questions (text, dropdown, multi-select, file upload)",
            "4. Submit button at bottom",
            "5. Confirmation page / redirect",
        ],

        "resume_upload": {
            "method": "File input with clickable label",
            "accepted_formats": ["PDF", "DOC", "DOCX", "TXT", "RTF"],
            "selector_hints": [
                "input[name='resume']",
                ".application-form input[type='file']",
            ],
            "notes": (
                "Max 100MB. Lever auto-populates 'Current Location' from resume if parseable. "
                "API submissions use multipart/form-data."
            ),
        },

        "tricky_ui": [
            "Single-page form - relatively simple for automation",
            "AVOID clicking 'Apply with LinkedIn' button - it opens OAuth popup",
            "Custom questions appear below standard fields, types vary (text, select, file)",
            "Dropdowns are usually native <select> elements",
            "Some employers have CAPTCHA (reCAPTCHA v2 or v3)",
            "Form updates are universal per company (same form for all postings)",
            "Rate limit: max 2 application POSTs per second via API",
            "No iframes in standard Lever hosted pages",
            "utm_source and ref query params used for source tracking",
        ],

        "field_selectors": {
            "pattern": "name-attribute and class-based",
            "examples": {
                "name": "input[name='name']",
                "email": "input[name='email']",
                "phone": "input[name='phone']",
                "org": "input[name='org']",
                "resume": "input[name='resume']",
                "urls_linkedin": "input[name='urls[LinkedIn]']",
                "urls_github": "input[name='urls[GitHub]']",
                "urls_portfolio": "input[name='urls[Portfolio]']",
                "comments": "textarea[name='comments']",
                "submit": "button[type='submit'], .postings-btn-submit",
            },
        },

        "api": {
            "base_url": "https://api.lever.co/v0/postings/{site}",
            "eu_base_url": "https://api.eu.lever.co/v0/postings/{site}",
            "submit_endpoint": "POST /v0/postings/{site}/{posting_id}?key={api_key}",
            "content_types": ["application/json", "multipart/form-data", "application/x-www-form-urlencoded"],
            "notes": "API does NOT expose custom questions. Rate limit: 429 if >2 POSTs/sec.",
        },
    },

    # =========================================================================
    # 3. GREENHOUSE
    # =========================================================================
    "greenhouse": {
        "name": "Greenhouse",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"boards\.greenhouse\.io/",
                r"boards-api\.greenhouse\.io/",
                r"job-boards\.greenhouse\.io/",
                r"\?gh_jid=\d+",                 # Greenhouse job ID param on custom domains
                r"greenhouse\.io/embed/job_app",  # Embedded iframe
            ],
            "dom_markers": [
                "#app_body",
                "[data-gh]",                     # data-gh="root", data-gh="section-wrapper", etc.
                "#application-form",
                ".application--form",
                "#main_fields",
                'meta[content*="greenhouse"]',
            ],
        },

        "account_required": False,
        "account_notes": "No account needed. Single-page form, though some companies add screening before the form.",

        "application_flow": [
            "1. Land on job page: boards.greenhouse.io/{board_token}/jobs/{job_id}",
            "   OR embedded: boards.greenhouse.io/embed/job_app?for={company}&token={job_id}",
            "   OR custom domain: company.com/jobs?gh_jid={job_id}",
            "2. Single-page application form:",
            "   - First Name (required)",
            "   - Last Name (required)",
            "   - Email (required)",
            "   - Phone (optional)",
            "   - Location / Address",
            "   - Resume upload (file or paste text)",
            "   - Cover Letter (file or paste text)",
            "   - LinkedIn Profile URL",
            "   - Website / Portfolio URL",
            "   - Education section (school, degree, discipline, dates)",
            "   - Employment section (company, title, dates)",
            "   - Custom questions (text, select, multi-select, file)",
            "   - EEOC / Demographic questions (optional, separate section)",
            "3. Submit button",
            "4. Confirmation / thank-you page",
        ],

        "resume_upload": {
            "method": "File input or text area; also supports URL and base64 via API",
            "accepted_formats": ["PDF", "DOC", "DOCX", "TXT", "RTF"],
            "selector_hints": [
                "input[type='file']#resume",
                "#resume_text",
                "input[data-field='resume']",
            ],
            "notes": "File must be >0 bytes. Greenhouse does NOT validate required fields server-side; all validation is client-side.",
        },

        "tricky_ui": [
            "OFTEN EMBEDDED IN IFRAMES on company career sites: boards.greenhouse.io/embed/job_app?for={company}&token={id}",
            "When embedded, must switch to iframe context before interacting with form",
            "Custom domain integrations inject gh_jid parameter; form may be in iframe or loaded via JS",
            "data-gh attributes help identify sections: data-gh='root', data-gh='section-wrapper', etc.",
            "Education/Employment sections have repeatable field groups (add another)",
            "Label-then-field pattern: label element is a previous sibling of the input wrapper",
            "Dropdowns may be custom (react-select) rather than native <select>",
            "EEOC section is separate and may be hidden behind a toggle or appear as separate questions",
            "No shadow DOM",
        ],

        "field_selectors": {
            "pattern": "id-based and name-based",
            "examples": {
                "first_name": "#first_name",
                "last_name": "#last_name",
                "email": "#email",
                "phone": "#phone",
                "resume": "input[type='file']#resume",
                "cover_letter": "input[type='file']#cover_letter",
                "linkedin": "input[name*='linkedin' i]",
                "custom_question": "input[name^='question_'], select[name^='question_']",
                "submit": "#submit_app",
            },
        },

        "api": {
            "submit_endpoint": "POST https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id}",
            "auth": "HTTP Basic Auth (API key as username, no password)",
            "content_types": ["multipart/form-data", "application/json"],
            "required_fields": ["first_name", "last_name", "email"],
        },
    },

    # =========================================================================
    # 4. iCIMS
    # =========================================================================
    "icims": {
        "name": "iCIMS",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"careers-.*\.icims\.com",         # e.g. careers-companyname.icims.com
                r"icims\.com/jobs/",
                r"\.icims\.com/",
            ],
            "dom_markers": [
                ".iCIMS_PrimaryButton",
                ".iCIMS_MainWrapper",
                ".iCIMS_JobsTable",
                "a.iCIMS_PrimaryButton",
                "#iCIMS_Header",
            ],
        },

        "account_required": True,
        "account_notes": (
            "Most iCIMS portals require account creation (email + password). "
            "Some support 'Apply with LinkedIn' or 'Apply with Indeed' to skip account creation. "
            "Candidate portal allows viewing application status."
        ),

        "application_flow": [
            "1. Land on job listing: careers-{company}.icims.com/jobs/{job_id}/job",
            "2. Click Apply button (.iCIMS_PrimaryButton)",
            "3. Sign In / Create Account page",
            "   - Create: name, email, password",
            "   - Or social sign-in (LinkedIn, Indeed)",
            "4. Multi-step application form (varies heavily by employer):",
            "   - Personal Information (name, contact, address)",
            "   - Resume upload",
            "   - Work Experience",
            "   - Education",
            "   - Custom screening questions (conditional logic - fields appear based on answers)",
            "   - Voluntary self-identification (EEO)",
            "5. Review & Submit",
        ],

        "resume_upload": {
            "method": "File input, typically on first or second page",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
                ".iCIMS_FileInput",
            ],
            "notes": (
                "iCIMS uses a LITERAL resume parser - requires conventional headings "
                "('Work Experience', 'Education'). Multi-column layouts and headers/footers "
                "cause parsing failures."
            ),
        },

        "tricky_ui": [
            "ENTIRE APPLICATION FORM IS IN AN IFRAME - must switch to iframe context",
            "URL parameter '&in_iframe=1' indicates iframe mode, but browser may strip it on redirect",
            "Workaround: detect single iframe on page, switch context to index 0",
            "Multi-step workflows with conditional questions (fields appear/disappear based on prior answers)",
            "Custom dropdowns (NOT native <select>), require click-to-open then click-option",
            "CAPTCHA challenges on some portals",
            "Aggressive session timeouts; application patterns (IP + timing) are tracked",
            "Bot detection: submissions at regular intervals from same IP get flagged",
            "Randomize wait times between pages to reduce detection risk",
            "Form structure varies HEAVILY between employers - no two iCIMS portals are identical",
        ],

        "field_selectors": {
            "pattern": "Class-based with iCIMS prefix",
            "examples": {
                "apply_button": ".iCIMS_PrimaryButton",
                "main_wrapper": ".iCIMS_MainWrapper",
            },
            "notes": "Selectors are highly inconsistent across employers. Use text content matching as fallback.",
        },
    },

    # =========================================================================
    # 5. TALEO (Oracle)
    # =========================================================================
    "taleo": {
        "name": "Taleo (Oracle)",
        "aliases": ["Oracle Taleo", "Oracle HCM"],

        "detection": {
            "url_patterns": [
                r"\.taleo\.net/careersection/",
                r"taleo\.net/.*jobdetail\.ftl",
                r"taleo\.net/.*jobapply\.ftl",
                r"taleo\.net/.*jobsearch\.ftl",
                r"taleo\.net/.*profile\.ftl",
            ],
            "dom_markers": [
                "#ftlform",
                ".ftr_container",
                "#requisitionDescriptionInterface",
                ".contentlinepanel",
            ],
        },

        "account_required": True,
        "account_notes": (
            "Account creation mandatory. One account per Taleo instance (employer). "
            "Legacy system with heavy form validation. Many large enterprises "
            "(Fortune 500) still use Taleo."
        ),

        "application_flow": [
            "1. Land on job search: {company}.taleo.net/careersection/{section_id}/jobsearch.ftl",
            "2. View job detail: jobdetail.ftl?lang=en&job={job_id}",
            "3. Click Apply -> redirected to login/create account",
            "   - profile.ftl page for sign-in or account creation",
            "   - Email, password, security question for new accounts",
            "4. Multi-page application flow (Pages contain Blocks, Blocks contain Fields):",
            "   Page 1: Personal Information (name, contact, address)",
            "   Page 2: Resume / Work Experience",
            "   Page 3: Education",
            "   Page 4: Application Questions (employer-specific screening)",
            "   Page 5: Diversity / EEO (optional)",
            "   Page 6: eSignature / Terms acceptance",
            "5. Review and Submit",
        ],

        "resume_upload": {
            "method": "File input on dedicated page, or copy-paste text",
            "accepted_formats": ["PDF", "DOC", "DOCX", "TXT", "RTF", "HTML"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Taleo may parse resume to pre-fill fields. Parsing quality varies.",
        },

        "tricky_ui": [
            "LEGACY JSP-based UI (.ftl = FreeMarker templates) - heavy server-side rendering",
            "Full page reloads between steps (NOT a SPA)",
            "URL contains .ftl extension and query parameters for state",
            "Form validation is SERVER-SIDE - submit triggers page reload with error messages",
            "Very slow page loads (2-5 seconds between steps)",
            "Session management is strict - timeout after inactivity",
            "CSRF tokens in hidden form fields - must submit the form as-is, not reconstruct it",
            "Dropdown menus may be custom Java-rendered widgets",
            "Calendar date pickers are Taleo-specific, not browser-native",
            "Multi-byte characters in URLs are NOT supported",
            "Some Career Sections require specific browser user-agents",
            "No shadow DOM, no iframes typically, but heavy reliance on JavaScript-generated content",
        ],

        "field_selectors": {
            "pattern": "ID-based with Taleo naming convention",
            "examples": {
                "form": "#ftlform",
                "job_detail": "#requisitionDescriptionInterface",
            },
            "notes": "IDs are generated and vary per instance. Use label text + input proximity.",
        },
    },

    # =========================================================================
    # 6. BambooHR
    # =========================================================================
    "bamboohr": {
        "name": "BambooHR",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r".*\.bamboohr\.com/careers/",
                r".*\.bamboohr\.com/jobs/",
                r".*\.bamboohr\.com/hiring/jobs/",
            ],
            "dom_markers": [
                "[class*='BambooHR']",
                ".fab-Page",
                ".jss-Page",        # BambooHR's styled-components classes
            ],
        },

        "account_required": False,
        "account_notes": (
            "No account needed for most BambooHR application forms. "
            "Single-page form. Some employers may require login via BambooHR portal."
        ),

        "application_flow": [
            "1. Land on careers page: {company}.bamboohr.com/careers/ or /hiring/jobs/",
            "2. Browse job listings, click on a job",
            "3. View job description",
            "4. Click 'Apply for This Job' button",
            "5. Single-page application form:",
            "   - First Name, Last Name",
            "   - Email, Phone",
            "   - Address (street, city, state, zip)",
            "   - Resume upload",
            "   - Cover Letter (optional, text or file)",
            "   - LinkedIn / Website URL",
            "   - Custom questions (employer-defined)",
            "   - EEO questions (optional)",
            "6. Submit",
            "7. Confirmation page / email",
        ],

        "resume_upload": {
            "method": "Standard file input",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Relatively straightforward upload. No complex parsing step visible to candidate.",
        },

        "tricky_ui": [
            "React-based SPA - DOM is rendered client-side",
            "Class names may be hashed/dynamic (CSS-in-JS like styled-components)",
            "Dropdowns are likely custom React components, not native <select>",
            "Form is typically on a single page - simpler than most ATS",
            "BambooHR integrates with Indeed and ZipRecruiter for 'Easy Apply'",
            "No known iframe issues",
            "No known shadow DOM",
            "Relatively low anti-bot measures compared to enterprise ATS",
        ],

        "field_selectors": {
            "pattern": "Dynamic class names; use aria-labels and input names",
            "examples": {},
            "notes": "BambooHR uses React with dynamic class names. Rely on aria-label, placeholder, and name attributes.",
        },

        "api": {
            "base_url": "https://api.bamboohr.com/api/gateway.php/{companyDomain}/v1",
            "notes": "API available for integration but requires API key (employer-side only).",
        },
    },

    # =========================================================================
    # 7. SmartRecruiters
    # =========================================================================
    "smartrecruiters": {
        "name": "SmartRecruiters",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"jobs\.smartrecruiters\.com/",
                r"careers\.smartrecruiters\.com/",
                r"api\.smartrecruiters\.com/",
            ],
            "dom_markers": [
                "[class*='smartrecruiters']",
                ".application-form",
                "#smart-apply",
            ],
        },

        "account_required": False,
        "account_notes": (
            "No account required for basic applications. SmartRecruiters has a job seeker portal "
            "(Smartr / smartr.me) for profile management, but it is optional. "
            "'Easy Apply' mode allows one-click applications with just a resume."
        ),

        "application_flow": [
            "1. Land on posting: jobs.smartrecruiters.com/{company}/{posting_uuid}",
            "2. View job description",
            "3. Click Apply (or 'Easy Apply' if enabled)",
            "4. Application form (single page or two pages depending on config):",
            "   - First Name (required), Last Name (required)",
            "   - Email (required)",
            "   - Phone",
            "   - Resume upload (required if enabled, always available in Easy Apply)",
            "   - Screening questions (may include branching / knockout questions)",
            "   - Cover letter",
            "   - Location / Address",
            "   - Custom fields (per employer)",
            "5. Submit",
            "6. Confirmation",
        ],

        "resume_upload": {
            "method": "File input; Easy Apply always includes resume upload",
            "accepted_formats": ["PDF", "DOC", "DOCX", "RTF", "JPG", "PNG"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Accepts images (JPG, PNG) in addition to standard document formats.",
        },

        "tricky_ui": [
            "Modern React-based UI, relatively clean HTML",
            "Screening questions may use branching logic (answer A shows question B)",
            "Knockout questions can immediately disqualify - fill carefully",
            "'Easy Apply' mode is simpler (fewer fields) than standard apply",
            "Some portals have CAPTCHA",
            "No known iframe issues for hosted pages (jobs.smartrecruiters.com)",
            "Custom career sites may embed SmartRecruiters in iframes",
            "No shadow DOM",
        ],

        "field_selectors": {
            "pattern": "Standard HTML with React rendering",
            "examples": {
                "first_name": "input[name='firstName']",
                "last_name": "input[name='lastName']",
                "email": "input[name='email']",
            },
            "notes": "Field names are relatively standard. Use name attributes as primary selectors.",
        },

        "api": {
            "base_url": "https://api.smartrecruiters.com/v1",
            "submit_endpoint": "POST /postings/{uuid}/candidates",
            "required_fields": ["firstName", "lastName", "email"],
            "notes": "Public API available. Screening question answers required if questions are marked required.",
        },
    },

    # =========================================================================
    # 8. ASHBY
    # =========================================================================
    "ashby": {
        "name": "Ashby",
        "aliases": ["AshbyHQ"],

        "detection": {
            "url_patterns": [
                r"jobs\.ashbyhq\.com/",
                r"ashbyhq\.com/",
                r"api\.ashbyhq\.com/",
            ],
            "dom_markers": [
                "[class*='ashby']",
                "._form_",          # Ashby uses CSS module-style class names
            ],
        },

        "account_required": False,
        "account_notes": "No account needed. Simple single-page form. Growing in popularity with tech startups.",

        "application_flow": [
            "1. Land on job listing: jobs.ashbyhq.com/{company}",
            "2. Click on specific job posting",
            "3. View job description",
            "4. Click 'Apply' button",
            "5. Single-page application form:",
            "   - Name (String, required)",
            "   - Email (Email, required)",
            "   - Phone (Phone)",
            "   - Resume (File, required)",
            "   - LinkedIn URL (SocialLink)",
            "   - GitHub URL (SocialLink)",
            "   - Portfolio / Website (SocialLink)",
            "   - Cover Letter (LongText or File)",
            "   - Custom questions with various field types",
            "   - Optional survey (separate from main application)",
            "6. Submit",
            "7. Confirmation",
        ],

        "resume_upload": {
            "method": "File input",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Resume is typically required.",
        },

        "tricky_ui": [
            "Modern React-based UI, relatively clean",
            "CSS module class names (hashed, like ._form_1a2b3c) - not stable for selectors",
            "Use field path attributes or aria-labels instead of class names",
            "Field types include: String, Email, File, Date, Number, Boolean, LongText, ValueSelect, MultiValueSelect, Phone, Score, SocialLink",
            "ValueSelect = single-select dropdown (custom component)",
            "MultiValueSelect = multi-select (custom component, checkboxes or tag-select)",
            "Optional demographic survey may appear as separate step after main form",
            "No iframes on hosted pages",
            "No shadow DOM",
            "No known anti-bot measures beyond standard rate limiting",
        ],

        "field_selectors": {
            "pattern": "Path-based internal identifiers",
            "examples": {
                "name": "[data-path='_systemfield_name'] input",
                "email": "[data-path='_systemfield_email'] input",
                "resume": "[data-path='_systemfield_resume'] input[type='file']",
            },
            "notes": (
                "Ashby fields have internal 'path' identifiers like _systemfield_name, _systemfield_email, "
                "_systemfield_resume. Custom fields have paths like _customfield_{uuid}."
            ),
        },

        "api": {
            "base_url": "https://api.ashbyhq.com",
            "endpoints": {
                "list_jobs": "POST /jobPosting.list",
                "job_info": "POST /jobPosting.info",
                "submit": "POST /applicationForm.submit",
                "survey": "POST /surveySubmission.create",
                "app_info": "POST /application.info",
            },
            "submit_content_type": "multipart/form-data",
            "required_fields": ["applicationForm (JSON)", "jobPostingId"],
            "notes": "All API endpoints are POST. Submit uses multipart/form-data with JSON applicationForm field.",
        },
    },

    # =========================================================================
    # 9. JOBVITE
    # =========================================================================
    "jobvite": {
        "name": "Jobvite",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"jobs\.jobvite\.com/",
                r"app\.jobvite\.com/",
                r"careers\.jobvite\.com/",
                r"jvst=",                  # Jobvite source tracking param
            ],
            "dom_markers": [
                ".jv-careersite",
                "[data-careersite]",
                ".jv-job-list",
                ".jv-apply-form",
            ],
        },

        "account_required": False,
        "account_notes": (
            "No account required for most Jobvite applications. "
            "Some employers enable a candidate portal for application tracking. "
            "'Apply with LinkedIn' available on some portals."
        ),

        "application_flow": [
            "1. Land on career site: jobs.jobvite.com/{company-name}",
            "   OR legacy: app.jobvite.com/CompanyJobs/Jobs.aspx?c={company_code}",
            "2. Browse jobs (single-column layout by category)",
            "3. Click on job title to view description",
            "4. Click 'Apply' button",
            "5. Application form (single page):",
            "   - First Name (required)",
            "   - Last Name (required)",
            "   - Email (required)",
            "   - Resume upload (configurable, may be optional)",
            "   - Custom fields (e.g., 'How did you hear about us?', referral, salary)",
            "6. Submit",
            "7. Confirmation",
        ],

        "resume_upload": {
            "method": "File input; LinkedIn profile can substitute for resume",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Resume may be optional if 'Apply with LinkedIn' is used.",
        },

        "tricky_ui": [
            "FREQUENTLY EMBEDDED IN IFRAMES on company career pages",
            "iFrame embed code: <div class='jv-careersite' data-careersite='your-careersite-name'>",
            "iFrame script: //jobs.jobvite.com/__assets__/scripts/careersite/public/iframe.js",
            "When in iframe, must switch context to interact with form",
            "The apply page can be linked directly (not in iframe) as alternative",
            "Legacy URLs (app.jobvite.com) use different DOM structure than modern (jobs.jobvite.com)",
            "Source tracking via URL params: __jvst=Job+Board&__jvsd={source_name}",
            "API is NOT fully open - must use the apply page for submission",
            "No shadow DOM",
        ],

        "field_selectors": {
            "pattern": "Class-based with jv- prefix",
            "examples": {
                "career_site": ".jv-careersite",
                "job_list": ".jv-job-list",
            },
            "notes": "Modern Jobvite uses React; legacy uses ASP.NET-style markup. Selectors differ significantly.",
        },
    },

    # =========================================================================
    # 10. SUCCESSFACTORS (SAP)
    # =========================================================================
    "successfactors": {
        "name": "SuccessFactors (SAP)",
        "aliases": ["SAP SuccessFactors", "SAP SF"],

        "detection": {
            "url_patterns": [
                r"\.jobs\.hr\.cloud\.sap",              # SAP-hosted
                r"\.successfactors\.com",
                r"\.successfactors\.eu",
                r"\.contactrh\.com/jobs/",               # Mirror/generic URL
                r"performancemanager\d*\.successfactors", # Legacy PM URLs
            ],
            "dom_markers": [
                "[class*='careerSite']",
                "[class*='jobReqList']",
                "#jobAlertContainer",
            ],
        },

        "account_required": True,
        "account_notes": (
            "Account creation required (same as 'joining the Talent Community'). "
            "Create Account page has fixed fields that cannot be reordered or customized. "
            "Includes Data Privacy Consent Statement fields. "
            "'Quick Apply' mode (if enabled) bypasses candidate profile step."
        ),

        "application_flow": [
            "1. Land on career site: {company}.jobs.hr.cloud.sap OR custom domain",
            "   Mirror URL format: {company}.contactrh.com/jobs/{board_id}/{posting_id}",
            "2. Browse and search jobs",
            "3. Click on job to view details",
            "4. Click Apply",
            "5. Login or Create Account",
            "   - Create Account = join Talent Community (fixed fields, cannot be customized)",
            "   - Data Privacy Consent during account creation",
            "6. Application form (single-stage or multi-stage depending on company config):",
            "   STANDARD FLOW:",
            "     - Candidate Profile page (personal info, work experience, education)",
            "     - Click 'Next' at bottom of profile page",
            "     - Review application",
            "     - Click 'Apply'",
            "   QUICK APPLY (if enabled):",
            "     - Single-page with account creation + job application fields combined",
            "7. Confirmation",
        ],

        "resume_upload": {
            "method": "File input on profile or application page",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Resume handling depends on company configuration. May parse or just store.",
        },

        "tricky_ui": [
            "SAP-hosted pages have different DOM structure than custom career sites",
            "'Reimagined Candidate Experience' (newer) vs legacy UI - different rendering",
            "Career Site Builder creates adaptive designs with branching questions via business rules",
            "Multi-stage applications have server-side page transitions",
            "Account creation page has FIXED layout - cannot be modified by employers",
            "Data Privacy Consent statement may block form progression until accepted",
            "Some instances use Angular/React, others use legacy JSP rendering",
            "No known iframe issues on SAP-hosted pages",
            "Custom domains may embed SuccessFactors content",
            "Session management is strict",
            "No shadow DOM in standard implementation",
        ],

        "field_selectors": {
            "pattern": "Varies between legacy and modern UI",
            "examples": {},
            "notes": (
                "Modern Career Site Builder uses React-like components. "
                "Legacy uses server-rendered HTML with inconsistent IDs. "
                "Use aria-labels and visible text matching as fallback."
            ),
        },
    },

    # =========================================================================
    # 11. ORACLE CLOUD HCM
    # =========================================================================
    "oraclecloud": {
        "name": "Oracle Cloud HCM",
        "aliases": ["Oracle Recruiting Cloud", "Oracle Fusion HCM"],

        "detection": {
            "url_patterns": [
                r".*\.oraclecloud\.com/hcmUI/",
                r".*\.oraclecloud\.com/.*fndApplyExternal",
                r"fa-.*\.oraclecloud\.com",
            ],
            "dom_markers": [
                "[class*='oraclecloud']",
                "[id*='HcmUI']",
            ],
        },

        "account_required": True,
        "account_notes": (
            "Account creation required per employer instance. Many Fortune 500 companies "
            "use Oracle Cloud HCM (Fortinet, Nokia, Emerson, etc.). Each company has its "
            "own Oracle Cloud tenant with separate credentials."
        ),

        "application_flow": [
            "1. Land on job search page within company's Oracle Cloud HCM portal",
            "2. View job description via /hcmUI/CandidateExperience/en/sites/{site}/job/{id}",
            "3. Click 'Apply' button",
            "4. Sign In / Create Account page",
            "   - Create Account: email, password, name",
            "   - Sign In: email + password",
            "5. Multi-step application form:",
            "   - Personal Information (name, contact, address)",
            "   - Resume / CV upload",
            "   - Work Experience",
            "   - Education",
            "   - Application Questions (custom screening per job)",
            "   - Voluntary Disclosures (EEO, veteran, disability)",
            "6. Review & Submit",
        ],

        "resume_upload": {
            "method": "File input on dedicated step",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Oracle Cloud HCM may parse resume to pre-fill fields. Upload step varies by configuration.",
        },

        "tricky_ui": [
            "Multi-step SPA form similar to Workday - URL may not change between steps",
            "Oracle JET (JavaScript Extension Toolkit) based UI - custom components throughout",
            "Dropdown menus are custom Oracle JET widgets, NOT native <select> elements",
            "Heavy use of aria attributes for accessibility",
            "Session timeout after inactivity; keep interactions flowing",
            "Form structure varies by employer configuration",
            "Some portals embed the application in iframes",
            "Custom date pickers and address autocomplete widgets",
        ],

        "field_selectors": {
            "pattern": "Oracle JET component IDs and aria attributes",
            "examples": {},
            "notes": (
                "Oracle Cloud HCM uses Oracle JET framework with dynamic IDs. "
                "Rely on aria-label, placeholder text, and label proximity for field identification."
            ),
        },
    },

    # =========================================================================
    # 12. WORKABLE
    # =========================================================================
    "workable": {
        "name": "Workable",
        "aliases": [],

        "detection": {
            "url_patterns": [
                r"apply\.workable\.com/",
                r"jobs\.workable\.com/",
            ],
            "dom_markers": [
                "[class*='workable']",
                "[data-ui='application-form']",
            ],
        },

        "account_required": False,
        "account_notes": "No account needed. Single-page application form, similar to Lever/Greenhouse.",

        "application_flow": [
            "1. Land on job posting: apply.workable.com/{company}/j/{posting_id}/",
            "2. View job description",
            "3. Click 'Apply for this job' or scroll to application form",
            "4. Single-page application form:",
            "   - First Name (required)",
            "   - Last Name (required)",
            "   - Email (required)",
            "   - Phone",
            "   - Resume / CV upload",
            "   - Cover Letter (optional)",
            "   - LinkedIn URL, Portfolio URL",
            "   - Custom screening questions",
            "5. Submit",
            "6. Confirmation page",
        ],

        "resume_upload": {
            "method": "File input",
            "accepted_formats": ["PDF", "DOC", "DOCX"],
            "selector_hints": [
                "input[type='file']",
            ],
            "notes": "Standard file upload. Resume typically required.",
        },

        "tricky_ui": [
            "Single-page form - relatively simple for automation, similar to Lever/Greenhouse",
            "Modern React-based UI with clean HTML structure",
            "Dropdowns may be custom React components rather than native <select>",
            "Some portals include CAPTCHA",
            "No known iframe issues on hosted pages (apply.workable.com)",
            "No shadow DOM",
        ],

        "field_selectors": {
            "pattern": "Standard HTML with data attributes",
            "examples": {
                "first_name": "input[name='firstname']",
                "last_name": "input[name='lastname']",
                "email": "input[name='email']",
                "phone": "input[name='phone']",
            },
            "notes": "Field names are standard. Use name attributes as primary selectors.",
        },
    },

    # =========================================================================
    # 13. myWorkdayJobs (same engine as Workday, separate entry for clarity)
    # =========================================================================
    # NOTE: myWorkdayJobs IS Workday. The URL pattern {company}.wd{N}.myworkdayjobs.com
    # is just the hosted Workday Recruiting portal. All Workday details above apply.
    # This entry exists as an explicit alias reference.
    "myworkdayjobs": {
        "name": "myWorkdayJobs",
        "aliases": ["Workday Recruiting"],
        "is_alias_of": "workday",
        "detection": {
            "url_patterns": [
                r"\.wd\d+\.myworkdayjobs\.com",   # company.wd5.myworkdayjobs.com
                r"myworkdayjobs\.com",
                r"myworkdaysite\.com",             # Workday variant
            ],
            "dom_markers": [
                "[data-automation-id]",
            ],
        },
        "notes": (
            "myWorkdayJobs IS Workday's hosted recruiting portal. The URL pattern is "
            "{company}.wd{instance_number}.myworkdayjobs.com/{locale}/{site_name}. "
            "Example: workday.wd5.myworkdayjobs.com/en-US/Workday. "
            "All Workday application flow details apply. See 'workday' profile."
        ),
    },
}


def detect_ats(url: str, page_html: str = "") -> str | None:
    """Detect which ATS a URL belongs to.

    Args:
        url: The job posting or application URL.
        page_html: Optional HTML content of the page for DOM-based detection.

    Returns:
        ATS key string (e.g. 'lever', 'greenhouse') or None if unrecognized.
    """
    import re

    url_lower = url.lower()

    for ats_key, profile in ATS_PROFILES.items():
        # Skip alias entries for primary detection
        if profile.get("is_alias_of"):
            continue

        detection = profile.get("detection", {})

        # Check URL patterns
        for pattern in detection.get("url_patterns", []):
            if re.search(pattern, url_lower):
                return ats_key

    # If URL didn't match, try DOM markers on page HTML
    if page_html:
        for ats_key, profile in ATS_PROFILES.items():
            if profile.get("is_alias_of"):
                continue
            detection = profile.get("detection", {})
            for marker in detection.get("dom_markers", []):
                # Convert CSS selector to a simple string search
                # Strip brackets and attribute selectors for basic matching
                search_term = marker.strip("[]#.").split("=")[0].split("*")[0]
                if search_term and search_term in page_html:
                    return ats_key

    return None


def get_profile(ats_key: str) -> dict | None:
    """Get the full ATS profile by key.

    If the key points to an alias, returns the aliased profile.
    """
    profile = ATS_PROFILES.get(ats_key)
    if profile and profile.get("is_alias_of"):
        return ATS_PROFILES.get(profile["is_alias_of"])
    return profile


# Convenience: list all ATS keys (excluding aliases)
ATS_KEYS = [k for k, v in ATS_PROFILES.items() if not v.get("is_alias_of")]
