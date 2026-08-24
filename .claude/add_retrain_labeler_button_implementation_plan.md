# Add Retrain Button for Options Meta Labeler

## Problem
The "Retrain Now" button was missing for the `options_meta_labeler` on the Models page.

## Solution
1. Update `Models.tsx` to include `options_meta_labeler` in the `canRetrain` check.
2. Route the retrain action for `options_meta_labeler` to its dedicated synchronous API endpoint (`api.retrainOptionsMetaModel`).
3. Add `options_meta_labeler` to the mock API data (`mock.ts`) to enable testing.
4. Update unit tests in `Models.test.tsx` to explicitly test the `options_meta_labeler` retraining behavior.

## Verification
- Run `npm run --prefix webapp typecheck`
- Run `npm run --prefix webapp test`
