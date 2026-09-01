# Add Options Desk Contract Test

## Background
We need to ensure that the API responses from the options desk endpoints strictly match the frontend TypeScript interfaces defined in `webapp/src/api/types.ts`. This contract test will verify that all required fields declared in the TS interfaces are present in the JSON payload returned by the FastAPI endpoints.

## Dependencies Check (§0)
All required endpoints are live in `api/pilots_api.py`, and all relevant TS interfaces are present in `webapp/src/api/types.ts`.
Endpoints verified:
- `/pilots/options/earnings-crush/candidates` -> `EarningsCrushCandidatesResponse`
- `/pilots/options/dispersion/opportunities` -> `DispersionBasketResponse`
- `/pilots/options/zero-dte/signals` -> `ZeroDteSignalResponse`
- `/pilots/options/gex/profile` -> `GexProfileResponse`
- `/pilots/options/market-maker/simulate` -> `MarketMakerSimResponse`
- `/pilots/execution/brokers/status` -> `MultiBrokerStatusResponse`
- `/pilots/ai/research/synthesize` -> `ResearchSynthesizeResponse`
- `/pilots/paper-broker/scenario-matrix` -> `ScenarioMatrixResponse`
- `/pilots/options/flow/unusual` -> `UnusualOptionsFlowResponse`

## Proposed Changes

### `tests/test_options_desk_response_contract.py`
#### [NEW] tests/test_options_desk_response_contract.py
- Add a script that uses regex to parse the exported interfaces in `webapp/src/api/types.ts`.
- Extracts the interface fields (handling optional `?` markers as well).
- Sends mock requests to the 9 defined options desk endpoints using FastAPI's `TestClient`.
- Asserts that the keys present in the JSON response are a superset of the fields defined in the TS interface.
- Ensures the test fails loudly if a TypeScript interface name is missing or altered.

## Verification Plan
### Automated Tests
- Run `pytest tests/test_options_desk_response_contract.py -q`
- Temporarily rename a field in `types.ts`, run the test to confirm it fails, and revert the change to prove the test catches regressions.
