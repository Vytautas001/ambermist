# Between phases. Destroys every GPU; keeps the weights volume, SSH keys and
# startup scripts.
#
# CAUTION: from P2 onward, "off" means giving back capacity you may not get
# again. Run `make preflight` BEFORE going off, and think about whether the
# hours you save are worth the re-acquisition risk.
phase         = "off"
planned_hours = 0
