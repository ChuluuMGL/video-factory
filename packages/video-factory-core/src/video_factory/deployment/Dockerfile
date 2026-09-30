ARG PYTHON_IMAGE
ARG FFMPEG_IMAGE
FROM ${FFMPEG_IMAGE} AS media_tools
FROM ${PYTHON_IMAGE}
COPY --from=media_tools /ffmpeg /ffprobe /usr/local/bin/
COPY --from=media_tools /doc /usr/local/share/ffmpeg-doc
COPY wheels /wheels
RUN python -m pip install --no-index --find-links /wheels /wheels/video_factory_core-*.whl && rm -rf /wheels
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["vfctl"]
