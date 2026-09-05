from scripts.zwca_score import ComplexityFeatures, calculate_score, tier_for_score


def test_empty_structural_features_route_to_solar() -> None:
    result = calculate_score(
        ComplexityFeatures(
            ast_node_count=0,
            dependency_depth=0,
            transform_density=0,
            branch_density=0,
            external_system_count=0,
            unsupported_construct_count=0,
        )
    )
    assert result.score == 0
    assert result.tier == "solar"


def test_high_complexity_routes_to_aurora() -> None:
    result = calculate_score(
        ComplexityFeatures(
            ast_node_count=5000,
            dependency_depth=12,
            transform_density=1,
            branch_density=1,
            external_system_count=5,
            unsupported_construct_count=8,
        )
    )
    assert result.score == 100
    assert result.tier == "aurora"


def test_tier_boundaries_are_stable() -> None:
    assert tier_for_score(15) == "solar"
    assert tier_for_score(15.01) == "daylight"
    assert tier_for_score(30) == "daylight"
    assert tier_for_score(30.01) == "horizon"
    assert tier_for_score(60) == "twilight"
    assert tier_for_score(60.01) == "starlight"
    assert tier_for_score(80) == "starlight"
    assert tier_for_score(80.01) == "aurora"
