#!/bin/bash
cargo install systemfd cargo-watch 
cd $(dirname $0)
export ARCHIVE_FILES=$(dirname $(pwd))/svndata

systemfd --no-pid -s http::8000 -- cargo watch -x run
