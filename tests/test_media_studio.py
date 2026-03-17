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
@patch("os.unlink")
@patch("subprocess.run")
def test_concat_videos_reencodes_instead_of_stream_copy(mock_run, mock_unlink, mock_normalize, tmp_path):
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

    assert mock_normalize.call_count == 2
    assert mock_unlink.called
    assert "-c:v" in cmd
    assert "libx264" in cmd
    assert "-pix_fmt" in cmd
    assert "yuv420p" in cmd
    assert "-r" in cmd
    assert "30" in cmd
    assert "-c" not in cmd
    assert list_path in cmd
    assert str(clip_a.resolve()) in list_contents
    assert str(clip_b.resolve()) in list_contents


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


def test_generate_video_task_errors_when_not_all_clips_are_generated(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_b = tmp_path / "b.jpg"
    photo_a.write_bytes(b"a")
    photo_b.write_bytes(b"b")

    statuses = []

    class FakeImage:
        def __init__(self, image_bytes=None, mime_type=None):
            self.image_bytes = image_bytes
            self.mime_type = mime_type

    class FakeTypes:
        Image = FakeImage

        class GenerateVideosConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    class FakeOperation:
        def __init__(self, has_video):
            self.done = True
            self.response = type(
                "Resp",
                (),
                {"generated_videos": [object()] if has_video else []},
            )()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        def generate_videos(self, **kwargs):
            self.calls += 1
            return FakeOperation(has_video=self.calls == 1)

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()

    def record_update(job_id, **kwargs):
        statuses.append(kwargs)

    with patch.object(media_studio, "_get_client", return_value=FakeClient()), \
         patch.dict("sys.modules", {"google.genai": type("FakeModule", (), {"types": FakeTypes})}), \
         patch.object(media_studio, "_update_job", side_effect=record_update), \
         patch.object(media_studio, "_save_video"), \
         patch.object(media_studio, "_trim_video"):
        media_studio._generate_video_task(
            "job123",
            [str(photo_a), str(photo_b)],
            prompt="",
            property_name="",
        )

    assert statuses[-1]["status"] == "error"
    assert "No se guardo un video parcial" in statuses[-1]["error"]
