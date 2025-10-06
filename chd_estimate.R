args <- commandArgs(trailingOnly=TRUE)
toBool <- function(x) { as.logical(as.integer(x)) }

age      <- as.integer(args[1])
sex      <- as.character(args[2])   # "male" | "female"
sbp      <- as.numeric(args[3])
bp_tx    <- toBool(args[4])
total_c  <- as.numeric(args[5])
hdl_c    <- as.numeric(args[6])
statin   <- toBool(args[7])
dm       <- toBool(args[8])
smoking  <- toBool(args[9])
egfr     <- as.numeric(args[10])
bmi      <- as.numeric(args[11])

suppressMessages(library(preventr))
res <- try({
  est <- preventr::estimate_risk(
    age=age, sex=sex, sbp=sbp, bp_tx=bp_tx,
    total_c=total_c, hdl_c=hdl_c, statin=statin, dm=dm,
    smoking=smoking, egfr=egfr, bmi=bmi,
    time="10yr", quiet=TRUE, collapse=TRUE
  )
  chd <- as.numeric(est$chd)
  if (is.na(chd)) stop("NA risk")
  cat(chd)  # 0–1 arası
}, silent=TRUE)

if (inherits(res, "try-error")) {
  msg <- paste("ERR:", conditionMessage(attr(res, "condition")))
  write(msg, stderr())
  quit(status=1)
}

