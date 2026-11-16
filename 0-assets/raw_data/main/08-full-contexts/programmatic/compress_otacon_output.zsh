#!/bin/zsh
# Compresses the extracted dump lines (otacon_output/RC and RS, *.ndjson) to *.ndjson.zst, file by
# file: compress, test the archive, then remove the original. Files already compressed are skipped,
# so it can be rerun. texts_to_parquet.py reads either form.
here=${0:A:h}
for f in $here/otacon_output/R[CS]/*.ndjson(N); do
  zstd -19 --long=27 -T2 -q -f $f -o $f.zst.tmp && zstd -t -q --long=27 $f.zst.tmp && mv $f.zst.tmp $f.zst && rm $f \
    && echo "$(date '+%F %T') ${f:t} done" || { echo "$(date '+%F %T') ${f:t} FAILED"; exit 1; }
done
echo "$(date '+%F %T') ALL_DONE"
