import unittest
from scripts.movie_studio_core__created_by_chatgpt__model_gpt_5_6_sol__task_TASK_0005 import (
 Gate, Verdict, Review, Shot, ProviderQuote, ProductionPolicyError,
 authorize_upscale, canonicalize, choose_zero_cost_provider, episode_can_complete
)

class MovieStudioCoreTests(unittest.TestCase):
 def review(self,g,v="v1",verdict=Verdict.PASS): return Review(g,v,verdict)
 def test_canonical_requires_visual_continuity_and_technical(self):
  s=Shot("S1","v1"); s.reviews={Gate.VISUAL_QA:self.review(Gate.VISUAL_QA),Gate.CONTINUITY_QA:self.review(Gate.CONTINUITY_QA)}
  with self.assertRaises(ProductionPolicyError): canonicalize(s)
 def test_dialogue_requires_audio(self):
  s=Shot("S1","v1",True); s.reviews={g:self.review(g) for g in (Gate.VISUAL_QA,Gate.CONTINUITY_QA,Gate.TECHNICAL_QA)}
  with self.assertRaises(ProductionPolicyError): canonicalize(s)
 def test_all_gates_make_canonical(self):
  s=Shot("S1","v1",True); s.reviews={g:self.review(g) for g in Gate}; canonicalize(s); self.assertTrue(s.canonical)
 def test_stale_review_is_rejected(self):
  s=Shot("S1","v2"); s.reviews={Gate.VISUAL_QA:self.review(Gate.VISUAL_QA,"v1")}
  with self.assertRaises(ProductionPolicyError): authorize_upscale(s)
 def test_upscale_requires_semantic_gates(self):
  s=Shot("S1","v1"); s.reviews={Gate.VISUAL_QA:self.review(Gate.VISUAL_QA)}
  with self.assertRaises(ProductionPolicyError): authorize_upscale(s)
 def test_upscale_after_semantic_gates(self):
  s=Shot("S1","v1"); s.reviews={g:self.review(g) for g in (Gate.VISUAL_QA,Gate.CONTINUITY_QA)}; authorize_upscale(s); self.assertTrue(s.upscale_allowed)
 def test_paid_provider_never_selected(self):
  with self.assertRaises(ProductionPolicyError): choose_zero_cost_provider([ProviderQuote("paid",0.01)])
 def test_zero_cost_provider_deterministic(self):
  q=choose_zero_cost_provider([ProviderQuote("zfree",0),ProviderQuote("afree",0)]); self.assertEqual("afree",q.provider)
 def test_episode_requires_verified_drive_master(self):
  self.assertFalse(episode_can_complete(all_shots_canonical=True,final_qc_passed=True,drive_master_verified=False))
  self.assertTrue(episode_can_complete(all_shots_canonical=True,final_qc_passed=True,drive_master_verified=True))

if __name__=="__main__": unittest.main()
