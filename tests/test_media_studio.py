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


def test_kenburns_filter_generates_valid_crop():
    from media_studio import _kenburns_filter

    for effect in ["zoom_in", "zoom_out", "pan_lr", "pan_rl", "pan_tb", "pan_bt"]:
        f = _kenburns_filter(effect, 720, 1280, 2160, 3840)
        assert "crop=" in f
        assert "scale=720:1280" in f


def test_pick_effects_avoids_consecutive_repeats():
    from media_studio import _pick_effects

    for _ in range(20):
        effects = _pick_effects(6)
        assert len(effects) == 6
        for i in range(1, len(effects)):
            assert effects[i] != effects[i - 1]


@patch("os.unlink")
@patch("subprocess.run")
def test_concat_videos_uses_stream_copy(mock_run, mock_unlink, tmp_path):
    from media_studio import _concat_videos

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"a")
    clip_b.write_bytes(b"b")
    out = tmp_path / "out.mp4"

    _concat_videos([str(clip_a), str(clip_b)], str(out))

    cmd = mock_run.call_args.args[0]
    list_path = str(out) + ".list.txt"
    list_contents = (tmp_path / "out.mp4.list.txt").read_text()

    assert mock_unlink.called
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-preset" in cmd
    assert "ultrafast" in cmd
    assert list_path in cmd
    assert str(clip_a.resolve()) in list_contents
    assert str(clip_b.resolve()) in list_contents


@patch("subprocess.run", side_effect=FileNotFoundError)
def test_concat_videos_raises_if_ffmpeg_missing(mock_run, tmp_path):
    from media_studio import _concat_videos

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    clip_a.write_bytes(b"a")
    clip_b.write_bytes(b"b")
    out = tmp_path / "out.mp4"

    with pytest.raises(RuntimeError, match="ffmpeg no esta instalado"):
        _concat_videos([str(clip_a), str(clip_b)], str(out))


def test_generate_clip_raises_if_ffmpeg_missing(tmp_path):
    from media_studio import _generate_clip

    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    clip = tmp_path / "clip.mp4"

    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match="ffmpeg no esta instalado"):
            _generate_clip(str(photo), str(clip), "zoom_in")


def test_generate_video_task_errors_when_clip_generation_fails(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_b = tmp_path / "b.jpg"
    photo_a.write_bytes(b"\xff\xd8\xff")
    photo_b.write_bytes(b"\xff\xd8\xff")

    statuses = []

    def record_update(job_id, **kwargs):
        statuses.append(kwargs)

    with patch.object(media_studio, "_update_job", side_effect=record_update), \
         patch.object(media_studio, "_enhance_photo", side_effect=lambda i, o: i), \
         patch.object(media_studio, "_generate_clip", return_value=False):
        media_studio._generate_video_task(
            "job123",
            [str(photo_a), str(photo_b)],
            prompt="",
            property_name="",
        )

    assert statuses[-1]["status"] == "error"


def test_generate_video_task_partial_clips_still_produces_video(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_b = tmp_path / "b.jpg"
    photo_a.write_bytes(b"\xff\xd8\xff")
    photo_b.write_bytes(b"\xff\xd8\xff")

    statuses = []
    call_count = [0]

    def fake_generate_clip(photo, clip, effect, fmt="vertical"):
        call_count[0] += 1
        if call_count[0] == 1:
            Path(clip).write_bytes(b"fake")
            return True
        return False

    def record_update(job_id, **kwargs):
        statuses.append(kwargs)

    with patch.object(media_studio, "_update_job", side_effect=record_update), \
         patch.object(media_studio, "_enhance_photo", side_effect=lambda i, o: i), \
         patch.object(media_studio, "_generate_clip", side_effect=fake_generate_clip), \
         patch.object(media_studio, "_polish_final_video"), \
         patch("os.unlink"), \
         patch("os.replace"):
        media_studio._generate_video_task(
            "job456",
            [str(photo_a), str(photo_b)],
            prompt="",
            property_name="",
        )

    # Partial clips should still produce a completed video (1 of 2 is OK)
    assert statuses[-1]["status"] == "completed"


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
    # args = (job_id, photo_paths, prompt, property_name, video_format, voice)
    assert args[4] == "horizontal"


def test_generate_video_task_polishes_final_video(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_a.write_bytes(b"\xff\xd8\xff")

    clip_path = [None]

    def fake_generate_clip(photo, clip, effect, fmt="vertical"):
        Path(clip).write_bytes(b"fake")
        clip_path[0] = clip
        return True

    with patch.object(media_studio, "_enhance_photo", side_effect=lambda i, o: i), \
         patch.object(media_studio, "_generate_clip", side_effect=fake_generate_clip), \
         patch.object(media_studio, "_polish_final_video") as mock_polish, \
         patch.object(media_studio, "_update_job"):
        media_studio._generate_video_task(
            "job789",
            [str(photo_a)],
            prompt="",
            property_name="Test",
            video_format="vertical",
        )

    assert mock_polish.called


def test_enhance_photo_falls_back_to_original_when_no_tools(tmp_path):
    import media_studio

    photo = tmp_path / "test.jpg"
    photo.write_bytes(b"\xff\xd8\xff")
    enhanced = tmp_path / "enhanced.jpg"

    with patch.object(media_studio, "_upscale_photo", return_value=False), \
         patch.dict("sys.modules", {"PIL": None, "PIL.Image": None}):
        result = media_studio._enhance_photo(str(photo), str(enhanced))

    assert result == str(photo)


def test_generate_image_returns_error_for_ffmpeg_backend():
    import media_studio

    with patch("analytics.save_media_job"):
        job_id = media_studio.generate_image("test prompt", "Test property")

    job = media_studio.get_job(job_id)
    assert job["status"] == "error"
    assert "FFmpeg" in job["error"]


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

    photo_a = tmp_path / "a.jpg"
    photo_a.write_bytes(b"\xff\xd8\xff")

    def fake_generate_clip(photo, clip, effect, fmt="vertical"):
        Path(clip).write_bytes(b"fake")
        return True

    with patch.object(media_studio, "_enhance_photo", side_effect=lambda i, o: i), \
         patch.object(media_studio, "_generate_clip", side_effect=fake_generate_clip), \
         patch.object(media_studio, "_generate_voiceover", return_value=True) as mock_vo, \
         patch.object(media_studio, "_polish_final_video") as mock_polish, \
         patch.object(media_studio, "_update_job"), \
         patch("os.unlink"):
        media_studio._generate_video_task(
            "jobVO",
            [str(photo_a)],
            prompt="Departamento luminoso en Palermo",
            property_name="Test",
            video_format="vertical",
        )

    assert mock_vo.called
    assert mock_vo.call_args.args[0] == "Departamento luminoso en Palermo"
    assert mock_polish.called
    # voiceover_path should be passed to polish
    assert mock_polish.call_args.kwargs.get("voiceover_path") or \
           (len(mock_polish.call_args.args) > 4 and mock_polish.call_args.args[4])


def test_generate_video_task_skips_voiceover_when_no_prompt(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_a.write_bytes(b"\xff\xd8\xff")

    def fake_generate_clip(photo, clip, effect, fmt="vertical"):
        Path(clip).write_bytes(b"fake")
        return True

    with patch.object(media_studio, "_enhance_photo", side_effect=lambda i, o: i), \
         patch.object(media_studio, "_generate_clip", side_effect=fake_generate_clip), \
         patch.object(media_studio, "_generate_voiceover") as mock_vo, \
         patch.object(media_studio, "_polish_final_video") as mock_polish, \
         patch.object(media_studio, "_update_job"):
        media_studio._generate_video_task(
            "jobNoVO",
            [str(photo_a)],
            prompt="",
            property_name="Test",
            video_format="vertical",
        )

    mock_vo.assert_not_called()
    assert mock_polish.called
