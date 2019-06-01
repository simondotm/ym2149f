# Things to do

## Tuned percussion

* Try to use tuned noise if bass is recently active?

## Bass Improvements

* Pre-Scan the entire tune to decide how each channel carries low frequencies
* Bias the low frequencies -> PN based on these weightings

## Sampling

* Create functions that will process a frame of data
* Allow a higher sample rate for the output VGM
* Incorporate envelope sampling at this higher rate if set
* Implement differential output so only actual changes are output

## Tooling

* Implement a command line parameter system
* - target clock speed
* - sampling quality default(50)/high(100)/full(44.1Khz)
* - verbose
* - info only
* - channel filters
* - disable simulated bass
* - disable envelopes
* - disable tuned percussion
* - enable digi drums (forces full sampling mode)
* - enable track wide transpose(to fix musical issues where only some of a melody is up-transposed in the bass end frequencies)


## Possible Enhancements

* Find out why certain tunes exhibit problem tuning or stuttering
* See if the volumes of envelopes may need boosting (does averaging create a quieter effect?)
* Implement samples as lookup tables (only for higher rate envelope sampling)
* info mode - only scans the tune
* Fix the VGM header characters bug
* Support gzipped YM files
* 

## Other ideas

Separate out the tools?

* YM2SN - convert YM music to SN VGM
* VGMCONV - convert SN VGM to different clock speeds
* VGMPACK - convert SN VGM to optimized formats (BBC BIN, or other flavours )