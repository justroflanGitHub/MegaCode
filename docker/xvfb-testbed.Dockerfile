# Xvfb-надстройка над тестовым стендом: настоящий X-сервер в контейнере,
# чтобы воспроизводить xcb-специфичные предупреждения отрисовки
# (QPainter::end: Painter ended with N saved states), которые не видны
# ни на offscreen, ни на Windows-платформе.
#
#   docker build -t megacode/xvfb-testbed -f docker/xvfb-testbed.Dockerfile .
#   docker run --rm -v "//d/claude/MegaCode:/work" -w //work \
#       megacode/xvfb-testbed sh repro_xcb.sh <script.py>
FROM megacode/testbed-debian10

RUN (apt-get update || (sleep 8 && apt-get update)) \
    && apt-get install -y --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*

COPY docker/xvfb-repro.sh /usr/local/bin/xvfb-repro.sh
RUN chmod 755 /usr/local/bin/xvfb-repro.sh

ENTRYPOINT ["/usr/local/bin/xvfb-repro.sh"]
