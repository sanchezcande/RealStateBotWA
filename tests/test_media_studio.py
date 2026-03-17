from unittest.mock import patch

import pytest


def test_build_video_prompt_enforces_fidelity_rules():
    from media_studio import _build_video_prompt

    prompt = _build_video_prompt(
        "Estilo elegante y premium",
        "Torre Libertador",
    )

    assert "single source of truth" in prompt
    assert "Do not add, remove, replace, restyle, or reposition anything." in prompt
    assert "no flicker, flashing" in prompt
    assert "must never override scene fidelity" in prompt
    assert "Torre Libertador" in prompt
    assert "Estilo elegante y premium" in prompt


@patch("media_studio._normalize_video_for_concat")
@patch("subprocess.run")
def test_concat_videos_reencodes_instead_of_stream_copy(mock_run, mock_normalize, tmp_path):
    from media_studio import _concat_videos

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"a")
    clip_b.write_bytes(b"b")
    out = tmp_path / "out.mp4"

    _concat_videos([str(clip_a), str(clip_b)], str(out))

    cmd = mock_run.call_args.args[0]
    assert mock_normalize.call_count == 2
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert "-r" in cmd
    assert "30" in cmd
    assert "-c" not in cmd


@patch("media_studio._normalize_video_for_concat")
@patch("subprocess.run", side_effect=FileNotFoundError)
def test_concat_videos_raises_if_ffmpeg_missing(mock_run, mock_normalize, tmp_path):
    from media_studio import _concat_videos

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"a")
    clip_b.write_bytes(b"b")
    out = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="ffmpeg no esta instalado"):
        _concat_videos([str(clip_a), str(clip_b)], str(out))
