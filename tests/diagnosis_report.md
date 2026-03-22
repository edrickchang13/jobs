# Diagnosis Report
Generated: 2026-03-21 22:23:53
Duration: 43s

## TESTS FAILED: 1 of 1

- **Lever - Direct Apply Form**

---

## FAIL: Lever - Direct Apply Form

### [pass] Navigate
Loaded: https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply
Screenshot: `C:\Users\Owner\jobs-forms\tests\screenshots\Lever - Direct Apply Form_01_loaded.png`

### [pass] Extract Fields
36 fields extracted

### [info] Field
[file        ] resume                                   required=False sel=[name="resume"]

### [info] Field
[text        ] Full name
✱                              required=True sel=[name="name"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Let the employer know what pronouns you  required=False sel=#customPronounsOption

### [info] Field
[email       ] Email
✱                                  required=True sel=[name="email"]

### [info] Field
[text        ] Phone
✱                                  required=True sel=[name="phone"]

### [info] Field
[text        ] Current location
✱                       required=True sel=[name="location"]

### [info] Field
[text        ] Current company                          required=False sel=[name="org"]

### [info] Field
[text        ] LinkedIn URL                             required=False sel=[name="urls[LinkedIn]"]

### [info] Field
[text        ] GitHub URL                               required=False sel=[name="urls[GitHub]"]

### [info] Field
[text        ] Portfolio URL                            required=False sel=[name="urls[Portfolio]"]

### [info] Field
[text        ] Other website                            required=False sel=[name="urls[Other]"]

### [info] Field
[radio       ] Are you available to work part-time (app required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] Are you available to work part-time (app required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] This is a hybrid internship based at our required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] This is a hybrid internship based at our required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] Are you legally authorized to work in th required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Are you legally authorized to work in th required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Will you now, or in the future, require  required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Will you now, or in the future, require  required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Were you referred to this position by an required=True sel=[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39]

### [info] Field
[radio       ] Were you referred to this position by an required=True sel=[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39]

### [info] Field
[textarea    ] cards b5d46307 d512 4507 873d 8aa9ba0f7b required=False sel=[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39]

### [info] Field
[textarea    ] ADDITIONAL INFORMATION                   required=False sel=[name="comments"]

### [info] Field
[select-one  ] eeo gender                               required=False sel=[name="eeo[gender]"]

### [info] Field
[select-one  ] eeo race                                 required=False sel=[name="eeo[race]"]

### [info] Field
[select-one  ] eeo veteran                              required=False sel=[name="eeo[veteran]"]

### [pass] LLM Mapping
21 mappings

### [info] Mapping
upload_file  | resume                              | 'resume'

### [info] Mapping
fill         | Full name
✱                         | 'Edrick Chang'

### [info] Mapping
click        | [name="pronouns"][label*="He/him"]  | ''

### [info] Mapping
fill         | Email
✱                             | 'eachang@scu.edu'

### [info] Mapping
fill         | Phone
✱                             | '(408) 806-6495'

### [info] Mapping
fill         | Current location
✱                  | 'Santa Clara, CA'

### [info] Mapping
skip         | Current company                     | ''

### [info] Mapping
fill         | LinkedIn URL                        | 'https://linkedin.com/in/edrickchang'

### [info] Mapping
fill         | GitHub URL                          | 'https://github.com/edrickchang'

### [info] Mapping
skip         | Portfolio URL                       | ''

### [info] Mapping
skip         | Other website                       | ''

### [info] Mapping
click        | [name="cards[7a24a6c6-2117-4412-bda | ''

### [info] Mapping
click        | [name="cards[7a24a6c6-2117-4412-bda | ''

### [info] Mapping
click        | [name="cards[81cb37d2-a188-494e-9e6 | ''

### [info] Mapping
click        | [name="cards[81cb37d2-a188-494e-9e6 | ''

### [info] Mapping
click        | [name="cards[b5d46307-d512-4507-873 | ''

### [info] Mapping
skip         | cards b5d46307 d512 4507 873d 8aa9b | ''

### [info] Mapping
skip         | ADDITIONAL INFORMATION              | ''

### [info] Mapping
select       | eeo gender                          | 'Male'

### [info] Mapping
select       | eeo race                            | 'Asian (Not Hispanic or Latino)'

### [info] Mapping
select       | eeo veteran                         | 'I am not a veteran'

### [info] Fill Form
Starting fill...
Screenshot: `C:\Users\Owner\jobs-forms\tests\screenshots\Lever - Direct Apply Form_03_before_fill.png`

### [fail] Fill Result
Filled: 10, Failed: 6
Screenshot: `C:\Users\Owner\jobs-forms\tests\screenshots\Lever - Direct Apply Form_04_after_fill.png`

### [fail] Fill Error
[name="pronouns"][label*="He/him"]: click failed

### [fail] Fill Error
[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258][field0]"]: click failed

### [fail] Fill Error
[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258][field1]"]: click failed

### [fail] Fill Error
[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce][field0]"]: click failed

### [fail] Fill Error
[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce][field1]"]: click failed

### [fail] Fill Error
[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39][field0]"]: click failed

### [pass] Verify
OK: Resume/CV
✱
EDRICKCHANG.PDF
Couldn't aut = 'C:\fakepath\EdrickChang.pdf'

### [pass] Verify
OK: Full name
✱ = 'Edrick Chang'

### [pass] Verify
OK: He/him = 'He/him'

### [pass] Verify
OK: She/her = 'She/her'

### [pass] Verify
OK: They/them = 'They/them'

### [pass] Verify
OK: Xe/xem = 'Xe/xem'

### [pass] Verify
OK: Ze/hir = 'Ze/hir'

### [pass] Verify
OK: Ey/em = 'Ey/em'

### [pass] Verify
OK: Hir/hir = 'Hir/hir'

### [pass] Verify
OK: Fae/faer = 'Fae/faer'

### [pass] Verify
OK: Hu/hu = 'Hu/hu'

### [pass] Verify
OK: Use name only = 'Use name only'

### [pass] Verify
OK: Custom = 'Custom'

### [pass] Verify
OK: Email
✱ = 'eachang@scu.edu'

### [pass] Verify
OK: Phone
✱ = '(408) 806-6495'

### [pass] Verify
OK: LinkedIn URL = 'https://linkedin.com/in/edrickchang'

### [pass] Verify
OK: GitHub URL = 'https://github.com/edrickchang'

### [pass] Verify
OK: Yes = 'Yes'

### [pass] Verify
OK: No = 'No'

### [pass] Verify
OK: Yes = 'Yes'

### [pass] Verify
OK: No = 'No'

### [pass] Verify
OK: Yes = 'Yes'

### [pass] Verify
OK: No = 'No'

### [pass] Verify
OK: Yes = 'Yes'

### [pass] Verify
OK: No = 'No'

### [pass] Verify
OK: Yes = 'Yes'

### [pass] Verify
OK: No = 'No'

### [pass] Verify
OK: Gender
Select ...
Male
Female
Decline to = 'Male'

### [pass] Verify
OK: Race
Select ...
Hispanic or Latino
White = 'Asian (Not Hispanic or Latino)'

### [pass] Verify
OK: Veteran status
Select ...
I am a veteran = 'I am not a veteran'

### [info] Verify
Location field may have autocomplete value: Current location
✱

### [pass] Verify
Resume: EdrickChang.pdf

### [fail] RESULT
SOME CHECKS FAILED
Screenshot: `C:\Users\Owner\jobs-forms\tests\screenshots\Lever - Direct Apply Form_05_final.png`

---

