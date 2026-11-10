import numpy as np

from koi.camera.card_reader import read_frame


def _setup():
    # Each patch is one or more normalized point locations. The fixture keeps
    # sensor and reference samples isolated so colours can be drawn exactly.
    names = ['white_ref', 'ph_reading', 'ph_ref_1', 'ph_ref_2', 'nh3_reading', 'nh3_ref_1']
    patches = [{'name': name, 'points': [[.2 + i * .1, .5]]} for i, name in enumerate(names)]
    return patches


def _draw(patches, colours, cast=(1, 1, 1)):
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    for p in patches:
        rgb = np.clip(np.asarray(colours[p['name']]) * cast, 0, 255).astype(np.uint8)
        for point in p['points']:
            x, y = (round(v * d) for v, d in zip(point, (200, 100), strict=True))
            if 5 <= x < 195 and 5 <= y < 95:
                frame[y-5:y+6, x-5:x+6] = rgb
    return frame


def test_matches_nearest_chroma_and_white_balance_cancels_cast():
    patches = _setup()
    colours = {'white_ref': [200, 200, 200], 'ph_reading': [160, 40, 40], 'ph_ref_1': [160, 40, 40],
               'ph_ref_2': [40, 160, 40], 'nh3_reading': [40, 160, 40], 'nh3_ref_1': [40, 160, 40]}
    base = read_frame(_draw(patches, colours), patches)
    cast = read_frame(_draw(patches, colours, (0.75, 1, 0.8)), patches)
    assert base['ph']['segment'] == cast['ph']['segment'] == 'ph_ref_1'
    assert base['nh3']['segment'] == cast['nh3']['segment'] == 'nh3_ref_1'


def test_outlier_and_off_image_samples_are_rejected_and_coverage_gate_applies():
    patches = _setup()
    patches[0]['points'] = [[0, 0], [.2, .5]]
    frame = _draw(patches, {'white_ref': [200, 200, 200], 'ph_reading': [150, 50, 50],
        'ph_ref_1': [150, 50, 50], 'ph_ref_2': [50, 150, 50], 'nh3_reading': [50, 150, 50],
        'nh3_ref_1': [50, 150, 50]})
    result = read_frame(frame, patches)
    assert result['total_dots'] > result['usable_dots']
    assert result['quality'] == 'usable'


def test_glare_and_one_patch_colour_outlier_are_dropped():
    patches = [{'name': 'white_ref', 'points': [[.2, .5]]},
               {'name': 'ph_reading', 'points': [[.35, .5]]},
               {'name': 'ph_ref_1', 'points': [[.5, .5], [.6, .5], [.7, .5]]},
               {'name': 'ph_ref_2', 'points': [[.8, .5]]}]
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    for x, rgb in ((40, [200, 200, 200]), (70, [150, 50, 50]), (100, [150, 50, 50]),
                   (120, [150, 50, 50]), (140, [150, 50, 50]), (160, [50, 150, 50])):
        frame[49:52, x-1:x+2] = rgb
    # Two consistent dots anchor the patch median; the third dot is a colour outlier.
    frame[49:52, 119:122] = [0, 0, 255]
    # More than 20% of pixels in this separate sample spot exceed white glare luminance.
    frame[49:52, 159:162] = [255, 255, 255]
    result = read_frame(frame, patches)
    assert result['patches']['ph_ref_1']['dots_used'] == 2
    assert result['patches']['ph_ref_2']['dots_used'] == 0


def test_mostly_covered_card_is_stored_as_unusable_even_without_white_reference():
    patches = _setup()
    patches[0]['points'] = [[0, 0]]
    for patch in patches[1:]:
        patch['points'] = [[0, 0]] * 4 + patch['points']
    result = read_frame(np.zeros((100, 200, 3), dtype=np.uint8), patches)
    assert result['quality'] == 'unusable'
    assert result['usable_dots'] == 0
    assert result['total_dots'] == 26


def _gray_dots(values):
    patches = [{'name': 'white_ref', 'points': [[.1, .5]]},
               {'name': 'ph_ref_1', 'points': [[.3 + i * .1, .5] for i in range(len(values))]}]
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    frame[49:52, 19:22] = 250
    for i, value in enumerate(values):
        x = 60 + i * 20
        frame[49:52, x-1:x+2] = value
    return read_frame(frame, patches)['patches']['ph_ref_1']


def test_dot_outlier_check_uses_the_median_of_the_other_dots():
    # With its own value in the median (114) the 128 dot is within 25%; against
    # the median of the other three dots (100) it is 28% off and is dropped.
    patch = _gray_dots([100, 100, 128, 160])
    assert patch['dots_used'] == 2
    assert patch['median_rgb'] == [100, 100, 100]
    # One bad dot among four does not cost the good ones.
    assert _gray_dots([100, 100, 100, 200])['dots_used'] == 3


def test_dot_outlier_check_stays_sane_for_one_to_three_dots():
    assert _gray_dots([100])['dots_used'] == 1
    assert _gray_dots([100, 110])['dots_used'] == 2
    # Two dots that disagree cannot say which one is wrong: both go.
    assert _gray_dots([60, 180])['dots_used'] == 0
    assert _gray_dots([100, 100, 200])['dots_used'] == 2
