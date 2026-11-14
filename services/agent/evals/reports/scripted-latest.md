# Field Shift agent eval report (scripted)

Generated 2026-11-06T22:53:01+00:00. Cases: 109, passed: 109, failed: 0.

The scripted provider plays the model, so these results validate orchestration, tool validation, grounding and contract handling. They do not measure a real model's judgement.

| Category | Passed | Total |
|---|---|---|
| tool_selection | 6 | 6 |
| constraints | 14 | 14 |
| data_reliability | 13 | 13 |
| security | 13 | 13 |
| provider | 15 | 15 |
| knowledge | 24 | 24 |
| integration | 24 | 24 |

## Cases

| Case | Category | Result | Terminal | Tools | Details |
|---|---|---|---|---|---|
| ts_general_question | tool_selection | PASS | turn.completed | get_field_facts | - |
| ts_map_layer | tool_selection | PASS | turn.completed | propose_ui_actions | - |
| ts_constraints_uk | tool_selection | PASS | turn.completed | propose_constraints, propose_ui_actions | - |
| ts_multi_intent | tool_selection | PASS | turn.completed | propose_constraints, propose_ui_actions | - |
| ts_unavailable_simulation | tool_selection | PASS | turn.completed | propose_ui_actions | - |
| ts_attachment_not_processed | tool_selection | PASS | turn.completed | - | - |
| ce_negation | constraints | PASS | turn.completed | propose_constraints | - |
| ce_english | constraints | PASS | turn.completed | propose_constraints | - |
| ce_slope_explicit | constraints | PASS | turn.completed | propose_constraints | - |
| ce_inferred | constraints | PASS | turn.completed | propose_constraints | - |
| ce_ambiguous | constraints | PASS | turn.completed | - | - |
| ce_conflict_same_call | constraints | PASS | turn.completed | propose_constraints | - |
| ce_conflict_across_calls | constraints | PASS | turn.completed | propose_constraints, propose_constraints | - |
| ce_unsupported_type | constraints | PASS | turn.completed | propose_constraints | - |
| ce_invalid_values | constraints | PASS | turn.completed | propose_constraints | - |
| ce_fabricated_evidence | constraints | PASS | turn.completed | propose_constraints | - |
| ce_explicit_without_evidence | constraints | PASS | turn.completed | propose_constraints, propose_constraints | - |
| ce_supersedes_existing | constraints | PASS | turn.completed | propose_constraints | - |
| ce_already_in_context | constraints | PASS | turn.completed | propose_constraints | - |
| ce_chip_needs_accepted_ids | constraints | PASS | turn.completed | propose_ui_actions | - |
| dr_render_provenance | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_missing_fact | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_unavailable_fact | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_other_field_id | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_ml_as_observation | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_live_claim | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_unsupported_number_redacted | data_reliability | PASS | turn.completed | - | - |
| dr_token_before_retrieval | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_unknown_token | data_reliability | PASS | turn.completed | get_field_facts | - |
| dr_allowed_numbers | data_reliability | PASS | turn.completed | - | - |
| dr_simulation_claim | data_reliability | PASS | turn.completed | - | - |
| dr_photo_lab_claim | data_reliability | PASS | turn.completed | - | - |
| dr_farmer_number_echo | data_reliability | PASS | turn.completed | - | - |
| se_injection_farmer_text | security | PASS | turn.completed | - | - |
| se_injection_field_name | security | PASS | turn.completed | - | - |
| se_unregistered_tool | security | PASS | turn.completed | - | - |
| se_invalid_arguments | security | PASS | turn.completed | get_field_facts, get_field_facts | - |
| se_other_field_argument | security | PASS | turn.completed | get_field_facts | - |
| se_unsupported_action | security | PASS | turn.completed | propose_ui_actions | - |
| se_unknown_layer | security | PASS | turn.completed | propose_ui_actions | - |
| se_confirmation_required | security | PASS | turn.completed | propose_ui_actions | - |
| se_label_markup | security | PASS | turn.completed | propose_ui_actions | - |
| se_revision_argument | security | PASS | turn.completed | propose_ui_actions | - |
| se_secret_request | security | PASS | turn.completed | - | - |
| se_override_rules | security | PASS | turn.completed | - | - |
| se_context_too_large | security | PASS | invalid_request/error.context_too_large | - | - |
| pb_parallel_calls | provider | PASS | turn.completed | get_field_facts, get_field_facts, propose_ui_actions | - |
| pb_malformed_arguments | provider | PASS | turn.completed | get_field_facts | - |
| pb_repeated_loop | provider | PASS | llm_error/error.agent_no_progress | get_field_facts, get_field_facts, get_field_facts | - |
| pb_max_tokens_text | provider | PASS | llm_error/error.llm_incomplete | - | - |
| pb_max_tokens_tool | provider | PASS | llm_error/error.llm_incomplete | - | - |
| pb_timeout | provider | PASS | upstream_timeout/error.llm_timeout | - | - |
| pb_rate_limit | provider | PASS | rate_limited/error.llm_rate_limited | - | - |
| pb_unavailable | provider | PASS | upstream_unavailable/error.llm_unavailable | get_field_facts | - |
| pb_auth | provider | PASS | llm_error/error.llm_auth | - | - |
| pb_refusal | provider | PASS | turn.completed | propose_constraints | - |
| pb_unexpected_stop | provider | PASS | llm_error/error.llm_unexpected_stop | - | - |
| pb_tool_use_without_calls | provider | PASS | llm_error/error.llm_unexpected_stop | - | - |
| pb_round_limit | provider | PASS | llm_error/error.agent_round_limit | get_field_facts, get_field_facts, get_field_facts | - |
| pb_tool_budget | provider | PASS | turn.completed | get_field_facts, get_field_facts | - |
| pb_empty_answer | provider | PASS | turn.completed | - | - |
| kn_supported_en | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_supported_uk | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_valid_quote | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_wrong_stage | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_wrong_row_repaired | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_fabricated_chunk_id | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_not_retrieved | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_fabricated_url | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_fabricated_quote | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_general_as_field_data | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_unrelated_citation | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_no_evidence | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_empty_results | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_greeting_no_search | knowledge | PASS | turn.completed | - | - |
| kn_injection_in_source | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_conflicting_sources | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_conflict_misattributed | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_search_cache | knowledge | PASS | turn.completed | search_knowledge, search_knowledge | - |
| kn_search_timeout | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_bad_search_arguments | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_tool_absent_without_index | knowledge | PASS | turn.completed | - | - |
| kn_fact_and_citation | knowledge | PASS | turn.completed | get_field_facts, search_knowledge | - |
| int_compare_explained | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_compare_over_http | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_tool_hidden_when_off | integration | PASS | turn.completed | - | - |
| int_capability_probe_fails | integration | PASS | turn.completed | - | - |
| int_service_down_mid_turn | integration | PASS | turn.completed | compare_rotations | - |
| int_simulation_claim_without_result | integration | PASS | turn.completed | compare_rotations | - |
| int_unknown_result_id | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_select_real_rotation | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_select_unknown_rotation | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_geo_invalid_schema | integration | PASS | turn.completed | get_field_facts | - |
| int_demo_labelled_live | integration | PASS | turn.completed | compare_rotations | - |
| int_result_for_other_field | integration | PASS | turn.completed | compare_rotations | - |
| int_revision_mismatch | integration | PASS | turn.completed | compare_rotations | - |
| int_expired_result | integration | PASS | turn.completed | compare_rotations | - |
| int_repeated_compare | integration | PASS | turn.completed | compare_rotations, compare_rotations | - |
| int_unknown_unit | integration | PASS | turn.completed | compare_rotations | - |
| int_conflicting_observations | integration | PASS | turn.completed | get_field_facts | - |
| int_geo_down_still_answers | integration | PASS | turn.completed | get_field_facts | - |
| int_estimate_as_observation | integration | PASS | turn.completed | compare_rotations | - |
| int_sim_timeout | integration | PASS | turn.completed | compare_rotations | - |
| int_live_synthetic_comparison | integration | PASS | turn.completed | compare_rotations, propose_ui_actions | - |
| int_live_drought_unavailable | integration | PASS | turn.completed | compare_rotations | - |
| int_forged_live_claim | integration | PASS | turn.completed | get_field_facts | - |
| int_forged_value_labelled | integration | PASS | turn.completed | get_field_facts | - |
| kn_weakly_related_citation | knowledge | PASS | turn.completed | search_knowledge | - |
| kn_related_citation_no_warning | knowledge | PASS | turn.completed | search_knowledge | - |
