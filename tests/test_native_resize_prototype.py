"""Pure edge/corner and signed-monitor cases for the isolated sizing prototype."""
import pytest
from tools.prototype_native_resize import hit_test, HT


@pytest.mark.parametrize('edge,point', [
    ('nw',(-499,-199)),('n',(-250,-199)),('ne',(-1,-199)),('w',(-499,-100)),
    ('e',(-1,-100)),('sw',(-499,-1)),('s',(-250,-1)),('se',(-1,-1)),
])
def test_every_edge_and_corner_uses_actual_negative_monitor_coordinates(edge,point):
    assert hit_test((-500,-200,0,0),*point,6)==HT[edge]


def test_interior_and_outside_do_not_claim_a_resize_edge():
    assert hit_test((-500,-200,0,0),-250,-100,6)==1
    assert hit_test((-500,-200,0,0),50,50,6)==0
