*** Settings ***
Documentation     Non-regression triage of 8 campaigns of a fictional pricing library, driven by Robot Framework.
...               Each test runs the real agent (read-only MCP server, scoped reads, rules in code) on one
...               campaign, with no human input, and checks the route and the decision. The expected route
...               is written here; the ground truth of the gaps stays in src/regression_triage/data.py, never
...               in the database the agent reads.
...
...               The judge is chosen at the command line: --variable JUDGE:fake (default, no model)
...               or --variable JUDGE:ollama (the real local model).
...
...               The XRAY-DEMO-n tags are EXAMPLES of Xray test keys. Xray links a test of the import to
...               the Jira test whose key is among its tags; replace them with the keys of your own project.
Library           regression_triage.robot_keywords.TriageLibrary
Suite Setup       Prepare Triage Environment    ${OUTPUT DIR}${/}work    ${JUDGE}    ${MODEL}
Suite Teardown    Cleanup Triage Environment
Test Teardown     Log    Test ended: ${TEST STATUS}

*** Variables ***
${JUDGE}          fake
${MODEL}          qwen3.5:9b
# Expected route of each campaign. Override one at the command line to see Robot fail:
#   --variable EXPECTED_3:block
${EXPECTED_1}     go
${EXPECTED_2}     go
${EXPECTED_3}     go
${EXPECTED_4}     human_review
${EXPECTED_5}     block
${EXPECTED_6}     human_review
${EXPECTED_7}     block
${EXPECTED_8}     block

*** Test Cases ***
Campaign 1 Scheduler Logs Is Released
    [Documentation]    Nothing changes: no gap, the model is never called.
    [Tags]    campaign-1    route-go    XRAY-DEMO-1
    Campaign Should Be Triaged    1    ${EXPECTED_1}

Campaign 2 CSV Export Is Released
    [Documentation]    Three gaps barely above tolerance: numerical noise.
    [Tags]    campaign-2    route-go    XRAY-DEMO-2
    Campaign Should Be Triaged    2    ${EXPECTED_2}

Campaign 3 ESTR Discounting Is Released
    [Documentation]    Large swap gaps announced by a release note.
    [Tags]    campaign-3    route-go    XRAY-DEMO-3
    Campaign Should Be Triaged    3    ${EXPECTED_3}

Campaign 4 Volatility Surface Waits For A Human
    [Documentation]    Vega gaps covered by an open ticket: a person must decide.
    [Tags]    campaign-4    route-human_review    XRAY-DEMO-4
    Campaign Should Be Triaged    4    ${EXPECTED_4}

Campaign 5 Bond Calendars Is Blocked
    [Documentation]    Bond gaps nothing explains, one bond no longer prices.
    [Tags]    campaign-5    route-block    XRAY-DEMO-5
    Campaign Should Be Triaged    5    ${EXPECTED_5}

Campaign 6 FX Forward Points Waits For A Human
    [Documentation]    An announced change, and a fixed defect that came back.
    [Tags]    campaign-6    route-human_review    XRAY-DEMO-6
    Campaign Should Be Triaged    6    ${EXPECTED_6}

Campaign 7 Premium Adjusted Delta Is Blocked
    [Documentation]    The note announces delta, not vega: the vega gaps are regressions.
    [Tags]    campaign-7    route-block    XRAY-DEMO-7
    Campaign Should Be Triaged    7    ${EXPECTED_7}

Campaign 8 Rates Feed Pool Is Blocked
    [Documentation]    A ticket carries an instruction to approve; a swap gap still blocks.
    [Tags]    campaign-8    route-block    XRAY-DEMO-8
    Campaign Should Be Triaged    8    ${EXPECTED_8}

*** Keywords ***
Campaign Should Be Triaged
    [Arguments]    ${campaign}    ${expected_route}
    ${expected_decision}=    Set Variable If    '${expected_route}' == 'human_review'    waiting_for_human    ${expected_route}
    ${result}=    Run Triage    ${campaign}
    ${route}=    Triage Route    ${result}
    Should Be Equal    ${route}    ${expected_route}    campaign ${campaign}: wrong route
    ${decision}=    Triage Decision    ${result}
    Should Be Equal    ${decision}    ${expected_decision}    campaign ${campaign}: wrong decision
    Gap Counts Should Match Ground Truth    ${result}
    No Write Should Have Been Attempted    ${result}
