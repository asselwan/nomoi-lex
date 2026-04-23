# SSMC Data Requirements

This document describes which columns the Lex validation engine expects from SSMC's Cerner/Oracle Health itemized charge export, and which of those columns are currently missing from the default report configuration. Missing columns cause specific validation engines to run in advisory or degraded mode rather than producing full results.

## Current Export Status

SSMC's export provides encounter identity (FIN, MRN, BE), clinical coding (DIAGNOSIS, DRG_CODE, CPT_CODE, HCPCS, CDMSCHEDPHARM_CODE, CDM_CODE), financial totals (CLAIM_GROSS, PATIENT_SHARE, ACTIVITY_NET_AMT), and disposition fields (DISCHARGE_DISPOSITION, DISCH_TO_LOCATION). These columns are sufficient for the core DRG validation pipeline to run.

However, several columns that the engine needs for full coverage are not present in the default Cerner report. Until these are added, the affected engines will either skip their checks or emit generic warnings instead of precise findings.

## Required Columns Currently Missing

These columns are needed for specific engines to produce actionable results. Without them, those engines operate in degraded mode.

**EMIRATES_ID** (maps to `claim.emirates_id`). The 784-YYYY-NNNNNNN-N national identifier is required for cross-checking member identity against the Shafafiya national ID requirement. Without it, the identity consistency engine cannot validate that the patient's Emirates ID matches the payer's member record.

**DX_POA** (Present on Admission indicator, paired with each DIAGNOSIS row). Values should be Y, N, U, W, or 1. Without this indicator, the Hospital Acquired Condition (HAC) detection engine cannot distinguish pre-existing conditions from conditions acquired during the hospital stay. It will emit a WARNING on every HAC-matching diagnosis code rather than a targeted finding.

**PATIENT_AGE** (or **PATIENT_DOB**) and **PATIENT_GENDER**. These are required by the CAHMS engine (under-18 mental health adjustor calculation) and by the gender and age consistency checks that flag implausible diagnosis-to-demographic combinations. Without them, the CAHMS engine is skipped entirely and demographic consistency checks produce no results.

**DRG_BASE_PAYMENT**. The provider-reported DRG base payment amount from the grouper or pricing engine. This is the single most important missing field. Without it, the engine can calculate the expected payment but cannot compute the difference between reported and calculated values. That reported-vs-calculated delta is the primary output of the tool for most users.

**OUTLIER_PAYMENT**, **LAMA_PAYMENT**, **CAHMS_ADJUSTOR**. These are the reported adjustor payment amounts for outlier days (Engine 4), LAMA/AMA discharge adjustments (Engine 5), and CAHMS under-18 adjustments (Engine 6). Without them, these engines can compute their expected adjustor values but cannot cross-check them against what the provider actually reported.

**Principal diagnosis flag** (suggested column name: `PRINCIPAL_DX_FLAG`). A column that tags one row's DIAGNOSIS as the principal diagnosis for that FIN. Without it, Lex infers principal from the first diagnosis encountered per FIN, which depends on the row ordering of the export and is unreliable. A boolean or 1/0 flag eliminates this ambiguity.

**MODIFIER_1** through **MODIFIER_4**. CPT modifiers per charge line. Without them, Engine 9 cannot validate modifier 25/24/50 usage patterns, which is a common source of payer denials.

## Recommended Columns Currently Missing

These are not strictly required but improve the precision of specific findings.

**REGROUPED_DRG**. The DRG code recomputed with any HAC-tagged diagnosis excluded. This is typically a second pass through the grouper after removing diagnoses flagged as hospital-acquired. Without it, the HAC engine emits a generic warning that a HAC-eligible code was found. With it, the engine can compute the exact payment delta between the original and regrouped DRG, giving the reviewer a concrete financial impact figure.

## Suggested Email to SSMC BI Team

The following template can be sent to the SSMC Business Intelligence or clinical reporting team to request the additional columns.

---

Subject: Additional columns needed in the unbilled inpatient charge export

Hi team,

We are using the itemized charge report from Cerner for our internal pre-submission DRG validation tool. The existing columns are working well. Could you add the following fields to the export so that we can enable the remaining validation engines?

1. **EMIRATES_ID** — the 784-YYYY-NNNNNNN-N patient national ID
2. **DX_POA** — Present on Admission indicator paired with each DIAGNOSIS row (Y/N/U/W/1)
3. **PRINCIPAL_DX_FLAG** — boolean or 1/0 identifying which DIAGNOSIS row is principal per FIN
4. **PATIENT_AGE** (or PATIENT_DOB) and **PATIENT_GENDER**
5. **DRG_BASE_PAYMENT** — the grouper/pricing engine's base payment per FIN
6. **OUTLIER_PAYMENT**, **LAMA_PAYMENT**, **CAHMS_ADJUSTOR** — the pricing engine's adjustor outputs per FIN
7. **MODIFIER_1**, **MODIFIER_2**, **MODIFIER_3**, **MODIFIER_4** — CPT modifiers per charge line
8. **REGROUPED_DRG** (optional) — DRG code recomputed with any HAC-tagged diagnosis excluded

The existing columns are fine as they are. The validator can run without these additional fields, but specific rule engines will operate in degraded mode until they are present.

Please let us know if any of these require changes to the Cerner report definition or if there are alternative column names for data that is already available in the extract.

Thanks!

---

## After the Columns Are Added

Once SSMC adds these columns to the export, the only change needed in Lex is removing the `default: null` entries from the corresponding fields in `src/lex/default_column_mapping.yaml`. No code changes are required. This is the value of the config-driven mapping layer.
