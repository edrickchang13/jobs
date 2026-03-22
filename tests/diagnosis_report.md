# Diagnosis Report
Generated: 2026-03-21 21:46:36
Duration: 11s

## TESTS FAILED: 1 of 1

- **Lever - Direct Apply Form**

---

## FAIL: Lever - Direct Apply Form

### [pass] Navigate
Loaded: https://jobs.lever.co/aofl/4b91076d-8937-4dbc-a502-a7d6a66e2e19/apply
Screenshot: `C:\Users\Owner\jobs-upload\tests\screenshots\Lever - Direct Apply Form_01_loaded.png`

### [pass] Extract Fields
36 fields extracted

### [info] Field
[file        ] resume                                   required=False sel=[name="resume"]

### [info] Field
[text        ] Full name
✱                              required=True sel=[name="name"]

### [info] Field
[checkbox    ] He/him :: He/him                         required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] She/her :: She/her                       required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] They/them :: They/them                   required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Xe/xem :: Xe/xem                         required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Ze/hir :: Ze/hir                         required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Ey/em :: Ey/em                           required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Hir/hir :: Hir/hir                       required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Fae/faer :: Fae/faer                     required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Hu/hu :: Hu/hu                           required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Use name only :: Use name only           required=False sel=[name="pronouns"]

### [info] Field
[checkbox    ] Custom :: Custom                         required=False sel=#customPronounsOption

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
[radio       ] Yes :: Yes                               required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] No :: No                                 required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] Yes :: Yes                               required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] No :: No                                 required=True sel=[name="cards[7a24a6c6-2117-4412-bdae-7fb97e699258]

### [info] Field
[radio       ] Yes :: Yes                               required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] No :: No                                 required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Yes :: Yes                               required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] No :: No                                 required=True sel=[name="cards[81cb37d2-a188-494e-9e6d-9c5e195368ce]

### [info] Field
[radio       ] Yes :: Yes                               required=True sel=[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39]

### [info] Field
[radio       ] No :: No                                 required=True sel=[name="cards[b5d46307-d512-4507-873d-8aa9ba0f7b39]

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

### [error] LLM Mapping
CRASHED: The api_key client option must be set either by passing api_key to the client or by setting the OPENAI_API_KEY environment variable
Traceback (most recent call last):
  File "C:\Users\Owner\jobs-upload\tests\self_heal.py", line 163, in run_test
    mappings = map_fields_to_profile(fields, "Software engineering internship", company, role)
               ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\Users\Owner\jobs-upload\applicator\form_filler.py", line 670, in map_fields_to_profile
    client = _get_llm_client()
             ^^^^^^^^^^^^^^^^^
  File "C:\Users\Owner\jobs-upload\ap

---

