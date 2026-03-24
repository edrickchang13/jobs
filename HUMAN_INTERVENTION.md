# Human Intervention Requirements by ATS Portal

Last updated: March 24, 2026

---

## What Auto-Apply DOES Automatically
- Fills all standard fields: name, email, phone, LinkedIn, GitHub
- Uploads resume PDF
- Answers yes/no questions (work authorization, sponsorship, background check, etc.)
- Fills EEO / demographic questions with hardcoded safe answers
- Generates LLM answers for custom text/textarea questions
- Selects pronouns, country/state, degree, school

---

## What ALWAYS Requires Human Intervention

### 🔴 Submit Button
**All portals** — The pipeline intentionally stops before submit. You must click Submit yourself after reviewing the filled form. This is a safety feature to prevent accidental submissions.

---

## Per-Portal Breakdown

### ✅ Greenhouse
**Automated:**
- All contact fields, resume upload, social links
- School name (typeahead), degree dropdown, discipline/major (React Select typeahead)
- EEO section (gender, race, disability, veteran)
- Custom screening questions via LLM

**May Need Human Help:**
- **Transcript upload** — if job requires transcript, the file upload targets a specific input that may vary
- **Portfolio samples** — some Greenhouse forms have a portfolio section
- **Multi-page Greenhouse forms** — some companies add extra pages after the standard form
- **Custom demographic questions** — company-specific EEO questions beyond standard race/gender

---

### ✅ Lever
**Automated:**
- All contact fields, resume upload, LinkedIn/GitHub
- Location field with autocomplete (press_sequentially + conditional Enter)
- Pronouns, salary expectations (if standard)
- EEO section
- Custom questions via LLM

**May Need Human Help:**
- **"Apply with LinkedIn" button** — the handler explicitly avoids this, but on some Lever forms it's the only prominent option. Bot will use the manual form.
- **CAPTCHA** — Lever occasionally shows hCaptcha after navigation
- **Cover letter free-text** — the field is filled with generated text; you may want to review/edit

---

### ✅ Ashby
**Automated:**
- Contact fields, resume upload, social links
- Dropdown selections (React Select)
- Work authorization questions

**May Need Human Help:**
- **Email verification flow** — some Ashby portals require verifying your email before the form loads. The bot cannot check your email for verification codes.
- **Company-specific custom questions** — questions with very specific context may get generic LLM answers
- **"Sign in with Google"** — Ashby sometimes offers Google SSO; the bot uses the manual form

---

### ✅ SmartRecruiters
**Automated:**
- Contact fields, resume upload
- Standard screening questions
- Work authorization yes/no

**May Need Human Help:**
- **Account creation** — SmartRecruiters requires creating an account before applying. The bot attempts to create/login but may fail on CAPTCHA or email verification.
- **Email verification code** — if SR sends a code to your email, the bot cannot retrieve it
- **Social login** — bot skips social login options and uses email/password

---

### ⚠️ Taleo
**Automated (once logged in):**
- Form field filling
- Dropdown selections
- Yes/No questions

**Requires Human Setup:**
- **Credentials in credentials.yaml** — Taleo requires creating an account on the company's specific Taleo instance. Add your credentials under `taleo:` in `credentials.yaml`.
- **Account creation per-company** — unlike other portals, each company has its own separate Taleo instance with separate accounts. You may need to create a new account for each company.
- **Email verification** — Taleo often sends a verification email when creating accounts

**May Need Human Help:**
- **Security questions** — some Taleo instances ask security questions during login
- **"Are you a robot?"** — Taleo sometimes shows CAPTCHA challenges

---

### ⚠️ iCIMS
**Automated (once logged in):**
- Contact information
- Resume upload
- Screening questions

**Requires Human Setup:**
- **Credentials in credentials.yaml** — Add your iCIMS credentials under `icims:` in `credentials.yaml`
- **Per-company accounts** — Like Taleo, iCIMS instances are per-company

**May Need Human Help:**
- **Multi-step account verification** — iCIMS has a robust account creation flow
- **Background check consent** — iCIMS sometimes routes to third-party background check providers that require separate logins

---

### 🔧 Workday
**Automated:**
- My Information: name, email, phone, address, country, state, work authorization radio buttons
- My Experience: resume upload + Workday resume parsing, education entry (school, degree, field of study, GPA, start/end dates), LinkedIn website entry
- Application Questions: yes/no radio/dropdown questions, free-text answers via LLM
- Self Identify / Voluntary Disclosures: gender, race, veteran status, disability status
- "How Did You Hear About Us?" promptList dropdown

**Requires Human Setup:**
- **Login/account** — Workday requires a Workday account on the company's instance. The bot attempts auto-login but cannot handle email verification codes. You should pre-create your account or be ready to verify.
- **"Verify your email" panel** — Workday sometimes pops up a panel asking you to verify email changes. The bot tries to dismiss this but may need your help.

**May Need Human Help:**
- **MFA / 2-factor auth** — If the company's Workday instance requires MFA, the bot will get stuck. You must complete MFA manually.
- **Resume parsing corrections** — Workday auto-parses your resume and sometimes creates incorrect work experience entries. The bot tries to delete empty entries but may miss incorrectly parsed ones.
- **"Previous Application" prompt** — if you've applied to this company before, Workday may show a "use previous profile?" dialog
- **Professional references** — some Workday forms have a references section the bot doesn't fill
- **Work samples / portfolio uploads** — the bot only handles the resume upload; additional file uploads require manual action
- **Address fields** — if your address has special characters or is in a non-US format

---

## Credentials.yaml Setup

To enable Taleo and iCIMS applications, populate `credentials.yaml`:

```yaml
taleo:
  email: eachang@scu.edu
  password: YOUR_PASSWORD_HERE

icims:
  email: eachang@scu.edu
  password: YOUR_PASSWORD_HERE

smartrecruiters:
  email: eachang@scu.edu
  password: YOUR_PASSWORD_HERE
```

---

## Summary Table

| Portal | Fully Automated | Needs Credentials | Needs Email Verify | Stop Before Submit |
|--------|----------------|-------------------|--------------------|--------------------|
| Greenhouse | ✅ | No | No | ✅ Always |
| Lever | ✅ | No | No | ✅ Always |
| Ashby | 🟡 Usually | No | Sometimes | ✅ Always |
| SmartRecruiters | 🟡 Usually | Yes (optional) | Sometimes | ✅ Always |
| Taleo | 🟡 With creds | ⚠️ Required | Yes | ✅ Always |
| iCIMS | 🟡 With creds | ⚠️ Required | Yes | ✅ Always |
| Workday | 🟡 Mostly | ⚠️ Required | Sometimes | ✅ Always |
| Generic | 🟡 Best-effort | No | No | ✅ Always |
