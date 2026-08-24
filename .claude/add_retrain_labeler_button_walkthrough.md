# Walkthrough: Options Meta Labeler Retrain Button

## Changes Made
- **`webapp/src/screens/Models.tsx`**: Updated the `canRetrain` condition to allow `m.role === "options_meta_labeler"`. Added a specific block in `handleRetrain` to call the synchronous `api.retrainOptionsMetaModel` endpoint and immediately refresh the list, avoiding the async Job polling flow.
- **`webapp/src/api/mock.ts`**: Included `options_meta_labeler` in the `MODELS` constant to ensure mock rendering during tests.
- **`webapp/src/screens/Models.test.tsx`**: Added a new test specifically simulating a click on the "Retrain Now" button for `options_meta_labeler` and verifying `retrainOptionsMetaModel` is called. Updated existing badge expectations and handled TestingLibrary queries properly (`findAllByText`) to account for the model name and role being identical strings.

## Validation
- ✅ Run `npm run --prefix webapp typecheck` (Passed)
- ✅ Run `npm run --prefix webapp test` (169 files, 1,846 tests passed)
