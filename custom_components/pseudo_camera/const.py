"""Constants for the Pseudo Camera integration."""

DOMAIN = "pseudo_camera"

CONF_MEDIAMTX_HOST = "mediamtx_host"
CONF_MEDIAMTX_RTSP_PORT = "mediamtx_rtsp_port"
CONF_MEDIAMTX_RTMP_PORT = "mediamtx_rtmp_port"
CONF_FRAME_DIR = "frame_dir"
CONF_CAMERAS = "cameras"

CONF_PATH = "path"
CONF_SOURCE_ENTITY = "source_entity"
CONF_WAKE_DELAY = "wake_delay"

DEFAULT_MEDIAMTX_HOST = "127.0.0.1"
DEFAULT_MEDIAMTX_RTSP_PORT = 8554
DEFAULT_MEDIAMTX_RTMP_PORT = 1935
DEFAULT_WAKE_DELAY = 3
DEFAULT_FRAME_DIR = "/config/pseudo_camera/frames"

ATTR_RELAY_ACTIVE = "relay_active"
ATTR_FRAME_PATH = "frame_path"
ATTR_SOURCE_ENTITY = "source_entity"
