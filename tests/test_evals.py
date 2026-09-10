from cip.evals import run


def test_frozen_offline_evaluation_gate():
    report = run()
    assert report["passed"], report
    assert not report["model_quality_measured"]
    assert all(report["canaries_detected"].values())
