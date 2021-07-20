FROM onedep_tools

# force using bash shell
SHELL ["/bin/bash", "-c"]

# setup up ssh key used for accessing private repositories in git (webapps and resources_ro)
RUN mkdir -p -m 0600 ~/.ssh \
    && ssh-keyscan github.com >> ~/.ssh/known_hosts

# copy a ssh key authorised with github to this machine
COPY ssh/ /ssh/
RUN cp /ssh/id_rsa.pub ~/.ssh/id_rsa.pub && chmod 600 ~/.ssh/id_rsa.pub
RUN cp /ssh/id_rsa ~/.ssh/id_rsa && chmod 600 ~/.ssh/id_rsa

# temp fix until rebuild onedep_tools package
RUN rm -rm $BUILD_DIR/*

# setup a venv
ENV VENV=/venv
ENV PATH=$VENV/bin:$PATH
RUN python3 -m venv $VENV
RUN pip install --no-cache-dir --upgrade setuptools pip
RUN pip install wheel
RUN pip install wwpdb.utils.config

# access to content server
ARG CS_USER
ARG CS_PW
ARG CS_URL

# locations for checkouts
ENV TOP_WWPDB_WEBAPPS_DIR=$ONEDEP_PATH/source/onedep-webfe/webapps
ENV RO_RESOURCE_PATH=$ONEDEP_PATH/ro_resources

WORKDIR $ONEDEP_PATH/onedep_admin
COPY . .

RUN python scripts/RunUpdate.py --skip-taxdb --skip-schema --skip-toolvers |& tee $ONEDEP_PATH/python_install.log

WORKDIR $ONEDEP_PATH
