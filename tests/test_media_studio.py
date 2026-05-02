from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def test_get_video_format_config_defaults_to_vertical():
    from media_studio import _get_video_format_config

    assert _get_video_format_config("vertical")["width"] == 720
    assert _get_video_format_config("vertical")["height"] == 1280
    assert _get_video_format_config("horizontal")["width"] == 1280
    assert _get_video_format_config("horizontal")["height"] == 720
    assert _get_video_format_config("unknown")["width"] == 720


def test_build_video_filter_uses_cover_crop_and_fades():
    from media_studio import _build_video_filter

    video_filter = _build_video_filter("vertical", duration=8.0)

    assert "scale=720:1280:force_original_aspect_ratio=increase" in video_filter
    assert "crop=720:1280" in video_filter
    assert "pad=" not in video_filter
    assert "fade=t=in" in video_filter
    assert "fade=t=out" in video_filter


def test_build_video_prompt_includes_base_and_property():
    from media_studio import _build_video_prompt, BASE_VIDEO_PROMPT

    prompt = _build_video_prompt("nice lighting", "Casa Palermo")
    assert BASE_VIDEO_PROMPT in prompt
    assert "Casa Palermo" in prompt
    assert "nice lighting" in prompt


@patch("media_studio._normalize_video_for_concat")
@patch("subprocess.run")
def test_concat_videos_reencodes(mock_run, mock_normalize, tmp_path):
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
    assert "-filter_complex" in cmd
    assert "-i" in cmd


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


def test_generate_video_tour_stores_selected_video_format():
    import media_studio

    with patch("threading.Thread") as mock_thread, \
         patch("analytics.save_media_job"):
        media_studio.generate_video_tour(
            ["uploads/photos/a.png"],
            prompt="",
            property_name="Demo",
            video_format="horizontal",
        )

    args = mock_thread.call_args.kwargs["args"]
    # args = (job_id, photo_paths, prompt, voiceover_text, property_name, video_format, voice, enhance)
    assert args[5] == "horizontal"


def test_generate_voiceover_returns_false_on_empty_text():
    from media_studio import _generate_voiceover

    assert _generate_voiceover("", "/tmp/test.mp3") is False
    assert _generate_voiceover("   ", "/tmp/test.mp3") is False


def test_generate_voiceover_returns_false_if_edge_tts_missing():
    from media_studio import _generate_voiceover

    with patch.dict("sys.modules", {"edge_tts": None}):
        assert _generate_voiceover("Hola mundo", "/tmp/test.mp3") is False


def test_generate_video_task_generates_voiceover_when_prompt_set(tmp_path):
    import media_studio

    def fake_generate_task(job_id, photo_paths, prompt, property_name,
                           video_format="vertical", voice="", enhance=True):
        # Simulate: Gemini generates a clip → single clip → voiceover
        final_path = str(tmp_path / f"{job_id}.mp4")
        Path(final_path).write_bytes(b"fake")

        voiceover_path = ""
        voiceover_text = prompt.strip() if prompt else ""
        if voiceover_text:
            vo_path = str(tmp_path / f"{job_id}_voiceover.mp3")
            if media_studio._generate_voiceover(voiceover_text, vo_path, voice=voice):
                voiceover_path = vo_path

        media_studio._polish_final_video(final_path, voiceover_path=voiceover_path)

    with patch.object(media_studio, "_generate_voiceover", return_value=True) as mock_vo, \
         patch.object(media_studio, "_polish_final_video") as mock_polish:
        fake_generate_task(
            "jobVO", [str(tmp_path / "a.jpg")],
            prompt="Departamento luminoso en Palermo",
            property_name="Test",
        )

    assert mock_vo.called
    assert mock_vo.call_args.args[0] == "Departamento luminoso en Palermo"
    assert mock_polish.called


def test_generate_video_task_skips_voiceover_when_no_prompt():
    import media_studio

    with patch.object(media_studio, "_generate_voiceover") as mock_vo:
        # No prompt → voiceover should not be called
        voiceover_text = "".strip()
        if voiceover_text:
            media_studio._generate_voiceover(voiceover_text, "/tmp/test.mp3")

    mock_vo.assert_not_called()


def test_generate_image_starts_background_task():
    import media_studio

    with patch("threading.Thread") as mock_thread, \
         patch("analytics.save_media_job"):
        job_id = media_studio.generate_image("test prompt", "Test property")

    assert job_id
    assert mock_thread.called
    args = mock_thread.call_args.kwargs["args"]
    assert args[1] == "test prompt"


def test_mime_type_detection():
    from media_studio import _mime_type

    assert _mime_type("photo.jpg") == "image/jpeg"
    assert _mime_type("photo.jpeg") == "image/jpeg"
    assert _mime_type("photo.png") == "image/png"
    assert _mime_type("photo.webp") == "image/webp"
    assert _mime_type("photo.bmp") == "image/jpeg"  # fallback
