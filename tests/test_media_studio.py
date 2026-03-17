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


def test_get_video_format_config_defaults_to_vertical():
    from media_studio import _get_video_format_config

    assert _get_video_format_config("vertical")["aspect_ratio"] == "9:16"
    assert _get_video_format_config("horizontal")["aspect_ratio"] == "16:9"
    assert _get_video_format_config("unknown")["aspect_ratio"] == "9:16"


def test_build_video_filter_uses_cover_crop_and_fades():
    from media_studio import _build_video_filter

    video_filter = _build_video_filter("vertical", duration=8.0)

    assert "scale=720:1280:force_original_aspect_ratio=increase" in video_filter
    assert "crop=720:1280" in video_filter
    assert "pad=" not in video_filter
    assert "fade=t=in" in video_filter
    assert "fade=t=out" in video_filter


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


def test_generate_video_task_aborts_on_quota_exhausted(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_b = tmp_path / "b.jpg"
    photo_c = tmp_path / "c.jpg"
    photo_a.write_bytes(b"a")
    photo_b.write_bytes(b"b")
    photo_c.write_bytes(b"c")

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
        def __init__(self):
            self.done = True
            self.response = type("Resp", (), {"generated_videos": [object()]})()

    class FakeModels:
        def __init__(self):
            self.calls = 0

        def generate_videos(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeOperation()
            raise RuntimeError("429 RESOURCE_EXHAUSTED")

    class FakeClient:
        def __init__(self):
            self.models = FakeModels()

    def record_update(job_id, **kwargs):
        statuses.append(kwargs)

    with patch.object(media_studio, "_get_client", return_value=FakeClient()), \
         patch.dict("sys.modules", {"google.genai": type("FakeModule", (), {"types": FakeTypes})}), \
         patch.object(media_studio, "_update_job", side_effect=record_update), \
         patch.object(media_studio, "_save_video"), \
         patch.object(media_studio, "_trim_video"), \
         patch("os.unlink"):
        media_studio._generate_video_task(
            "job456",
            [str(photo_a), str(photo_b), str(photo_c)],
            prompt="",
            property_name="",
        )

    assert statuses[-1]["status"] == "error"
    assert "RESOURCE_EXHAUSTED" in statuses[-1]["error"]


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
    assert args[-1] == "horizontal"


def test_generate_video_task_polishes_final_video(tmp_path):
    import media_studio

    photo_a = tmp_path / "a.jpg"
    photo_a.write_bytes(b"a")

    class FakeImage:
        def __init__(self, image_bytes=None, mime_type=None):
            self.image_bytes = image_bytes
            self.mime_type = mime_type

    class FakeTypes:
        Image = FakeImage

        class GenerateVideosConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    class FakeVideoFile:
        def save(self, out_path):
            pass

    class FakeVideo:
        video = FakeVideoFile()

    class FakeOperation:
        done = True
        response = type("Resp", (), {"generated_videos": [FakeVideo()]})()

    class FakeModels:
        def generate_videos(self, **kwargs):
            return FakeOperation()

    class FakeClient:
        models = FakeModels()

    with patch.object(media_studio, "_get_client", return_value=FakeClient()), \
         patch.dict("sys.modules", {"google.genai": type("FakeModule", (), {"types": FakeTypes})}), \
         patch.object(media_studio, "_save_video"), \
         patch.object(media_studio, "_polish_final_video") as mock_polish, \
         patch.object(media_studio, "_update_job"):
        media_studio._generate_video_task(
            "job789",
            [str(photo_a)],
            prompt="",
            property_name="",
            video_format="vertical",
        )

    assert mock_polish.called
