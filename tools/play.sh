#!/bin/sh
# play an mml file through the game's own audio engine, with the composer's piano roll:
#   ./tools/play.sh song.mml
#   ./tools/play.sh song.mrproj    (a midiroll project: converted with mrproj2mml.py first)
# restarts whenever the file is saved; close the window to quit
set -e
[ -f "$1" ] || { echo "usage: $0 song.mml|song.mrproj"; exit 1; }
mml=$(realpath "$1")
root=$(realpath "$(dirname "$0")/..")
name=$(basename "$mml" | sed 's/\.[^.]*$//' | tr a-z A-Z | tr -d '"\\')
app=$(mktemp -d)
trap 'rm -rf "$app"' EXIT
cp "$root/games/composer/sprite.png" "$app/"
cd "$root"

build() {
    src=$mml
    case "$mml" in *.mrproj)
        src="$app/song.mml"
        ./tools/mrproj2mml.py "$mml" > "$src" || return 1
    esac
    if grep -q '["\\]' "$src"; then echo "$src: no \" or \\ allowed"; return 1; fi
    {
        echo "include \"$root/games/composer/player.smolt\""
        printf 'var MUSIC_FILE = "\n'
        cat "$src"
        echo '"'
        echo "var SONGS: Song[] = { { \"$name\", MUSIC_FILE } }"
    } > "$app/main.smolt"
    ./tools/compile.py "$app/main.smolt" > /dev/null
}

[ -d build ] || cmake -S . -B build
cmake --build build > /dev/null
build
while :; do
    ./build/smolt "$app" &
    pid=$!
    stamp=$(stat -c %y "$mml")
    restart=
    while kill -0 $pid 2> /dev/null; do
        sleep 0.3
        [ "$(stat -c %y "$mml")" = "$stamp" ] && continue
        stamp=$(stat -c %y "$mml")
        if build; then
            restart=1
            kill $pid
            wait $pid 2> /dev/null || true
        fi
    done
    [ "$restart" ] || exit 0   # the window was closed
done
