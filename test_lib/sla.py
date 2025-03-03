#!/usr/bin/env python
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields


LOGGER = logging.getLogger(__name__)


def sla_result_to_dict(sla_result):
    # Result example: <type 'list'>: [Row(service_level='sla1', shares=1)]
    sla_list = []
    for row in sla_result:
        current_sla = []
        for i, _ in enumerate(row._fields):
            current_sla.append(row[i])
        sla_list.append(current_sla)
    return sla_list


DEFAULT_SERVICE_LEVEL_SHARES = 1000


@dataclass
class ServiceLevelAttributes:
    shares: int = None
    timeout: str = None
    workload_type: str = None
    query_string: str = field(init=False, repr=False)

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        if key != "query_string":
            self._generate_query_string()

    def __post_init__(self):
        self._generate_query_string()

    def _generate_query_string(self):
        attr_strings = []

        for item in fields(self):
            value = getattr(self, item.name) if item.repr else None
            if value is not None:
                if item.type == "str":
                    attr_strings.append(f" AND {item.name} = '{value}'")
                else:
                    attr_strings.append(f" AND {item.name} = {value}")
        if attr_strings:
            attr_strings[0] = attr_strings[0].replace(" AND", " WITH")

        self.query_string = "".join(attr_strings)


class ServiceLevel:
    # The class provide interface to manage SERVICE LEVEL
    # pylint: disable=too-many-arguments
    def __init__(self, session,
                 name: str,
                 shares: int = 1000,
                 timeout: str = None,
                 workload_type: str = None):
        self.session = session
        self._name = f"'{name}'"
        self.verbose = True
        self._created = False
        self._sl_attributes = ServiceLevelAttributes(
            shares=shares,
            timeout=timeout,
            workload_type=workload_type
        )

    @classmethod
    def from_row(cls, session, row):
        row_dict = row._asdict()
        row_dict["name"] = row_dict.pop("service_level")
        return ServiceLevel(session=session, **row_dict)

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    @property
    def shares(self) -> int:
        return self._sl_attributes.shares

    @shares.setter
    def shares(self, service_level_shares):
        self._sl_attributes.shares = service_level_shares

    @property
    def created(self) -> bool:
        return self.created

    @created.setter
    def created(self, created: bool):
        self._created = created

    @property
    def timeout(self) -> str:
        return self._sl_attributes.timeout

    @timeout.setter
    def timeout(self, timeout: int):
        self._sl_attributes.timeout = timeout

    @property
    def workload_type(self) -> str:
        return self._sl_attributes.workload_type

    @workload_type.setter
    def workload_type(self, workload_type: str):
        self._sl_attributes.workload_type = workload_type

    def __eq__(self, other):
        return (self.name == other.name) and self._sl_attributes == other._sl_attributes

    def __repr__(self):
        return "%s: name: %s, attributes: %s" % (self.__class__.__name__, self.name, self._sl_attributes)

    def create(self, if_not_exists=True) -> ServiceLevel:
        query = "CREATE SERVICE_LEVEL{if_not_exists} {service_level_name}{query_attributes}"\
                .format(if_not_exists=' IF NOT EXISTS' if if_not_exists else '',
                        service_level_name=self.name,
                        query_attributes=self._sl_attributes.query_string)
        if self.verbose:
            LOGGER.debug('Create service level query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Service level %s has been created', self.name)
        self.created = True
        return self

    def alter(self, new_shares: int = None, new_timeout: str = None, new_workload_type: str = None):
        sla = ServiceLevelAttributes(shares=new_shares, timeout=new_timeout, workload_type=new_workload_type)
        query = 'ALTER SERVICE_LEVEL {service_level_name} {query_string}'\
                .format(service_level_name=self.name,
                        query_string=sla.query_string)
        if self.verbose:
            LOGGER.debug('Change service level query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Service level %s has been altered', self.name)
        self.shares = new_shares

    def drop(self, if_exists=True):
        query = 'DROP SERVICE_LEVEL{if_exists} {service_level_name}'\
                .format(service_level_name=self.name,
                        if_exists=' IF EXISTS' if if_exists else '')
        if self.verbose:
            LOGGER.debug('Drop service level query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Service level %s has been dropped', self.name)
        self.created = False

    def list_service_level(self) -> ServiceLevel | None:
        query = 'LIST SERVICE_LEVEL {}'.format(self.name)
        if self.verbose:
            LOGGER.debug('List service level query: %s', query)
        res = self.session.execute(query).all()
        assert len(res) <= 1, "Received %s service levels when expecting to receive only 1" % len(res)

        if len(res) == 0:
            return None

        result_dict = res[0]._asdict()
        result_dict["name"] = result_dict.pop("service_level")

        return ServiceLevel(session=self.session, **result_dict)

    def list_all_service_levels(self) -> list[ServiceLevel]:
        query = 'LIST ALL SERVICE_LEVELS'
        if self.verbose:
            LOGGER.debug('List all service levels query: %s', query)
        res_list = self.session.execute(query).all()
        output = []

        for res in res_list:
            result_dict = res._asdict()
            result_dict["name"] = result_dict.pop("service_level")
            output.append(ServiceLevel(session=self.session, **result_dict))
        return output

# pylint: disable=too-many-arguments, too-many-instance-attributes, unused-argument


class UserRoleBase:
    # Base class for ROLES and USERS
    AUTHENTICATION_ENTITY = ''

    def __init__(self, session, name, password=None, superuser=None, verbose=False, **kwargs):
        self._name = f'"{name}"'
        self.password = password
        self.session = session
        self.superuser = superuser
        self.verbose = verbose
        self._attached_service_level = None
        self._attached_service_level_name = ''
        self._attached_service_level_shares = None

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        self._name = name

    @property
    def attached_service_level(self):
        return self._attached_service_level

    @property
    def attached_service_level_name(self):
        return self._attached_service_level.name

    def attach_service_level(self, service_level):
        """
        :param auth_name: it may be role name or user name
        """
        query = f'ATTACH SERVICE_LEVEL {service_level.name} TO {self.name}'
        if self.verbose:
            LOGGER.debug('Attach service level query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Service level %s has been attached to %s role', service_level.name, self.name)
        self._attached_service_level = service_level

    def detach_service_level(self):
        """
        :param auth_name: it may be role name or user name
        """
        query = f'DETACH SERVICE_LEVEL FROM {self.name}'
        if self.verbose:
            LOGGER.debug('Detach service level query: %s', query)
        self.session.execute(query)
        LOGGER.debug('The service level has been detached from %s role', self.name)
        self._attached_service_level = None

    def grant_me_to(self, grant_to):
        role_be_granted = self.name
        grant_to = grant_to.name
        query = f'GRANT {role_be_granted} to {grant_to}'
        if self.verbose:
            LOGGER.debug('GRANT role query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Role %s has been granted to %s', role_be_granted, grant_to)

    def revoke_me_from(self, revoke_from):
        role_be_revoked = self.name
        role_revokes_from = revoke_from.name
        query = f'REVOKE ROLE {role_be_revoked} FROM {role_revokes_from}'
        if self.verbose:
            LOGGER.debug('REVOKE role query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Role %s has been revoked from %s', role_be_revoked, role_revokes_from)

    def attach_another_sla_to_role(self, service_level):
        self.detach_service_level()
        self.attach_service_level(service_level=service_level)

    def list_user_role_attached_service_levels(self):
        query = f'LIST ATTACHED SERVICE_LEVEL OF {self.name}'
        if self.verbose:
            LOGGER.debug('List attached service level(s) query: %s', query)
        return self.session.execute(query).all()

    def list_all_attached_service_levels(self):
        query = 'LIST ATTACHED ALL SERVICE_LEVELS'
        if self.verbose:
            LOGGER.debug('List attached service level(s) query: %s', query)
        res = list(self.session.execute(query))
        return res

    def drop(self, if_exists=True):
        query = f"""DROP {self.AUTHENTICATION_ENTITY}{' IF EXISTS' if if_exists else ''} {self.name}"""
        if self.verbose:
            LOGGER.debug('Drop %s query: %s', self.AUTHENTICATION_ENTITY, query)

        self.session.execute(query)
        LOGGER.debug('%s %s has been dropped', self.AUTHENTICATION_ENTITY, self.name)


class Role(UserRoleBase):
    # The class provide interface to manage ROLES
    AUTHENTICATION_ENTITY = 'ROLE'

    def __init__(self, session, name, password=None, login=False, superuser=False, options_dict=None, verbose=True):
        super().__init__(session, name, password, superuser, verbose)
        self.login = login
        self.options_dict = options_dict

    def create(self) -> Role:
        # Example: CREATE ROLE bob WITH PASSWORD = 'password_b'AND LOGIN = true AND SUPERUSER = true;
        # Example: CREATE ROLE carlos WITH OPTIONS = {'custom_option1': 'option1_value', 'custom_option2': 99};
        role_options = {}
        for opt in ['password', 'login', 'superuser', 'options_dict']:
            if hasattr(self, opt):
                value = getattr(self, opt)
                if value:
                    role_options[opt.replace('_dict', '')] = '\'{}\''.format(value) \
                        if opt == 'password' else value
        role_options_str = ' AND '.join(['{} = {}'.format(opt, val) for opt, val in role_options.items()])
        if role_options_str:
            role_options_str = ' WITH {}'.format(role_options_str)

        query = f"CREATE ROLE {self.name}{role_options_str}"
        if self.verbose:
            LOGGER.debug('CREATE role query: %s', query)
        self.session.execute(query)
        LOGGER.debug('Role %s has been created', self.name)
        return self


class User(UserRoleBase):
    # The class provide interface to manage USERS
    AUTHENTICATION_ENTITY = 'USER'

    def __init__(self, session, name, password=None, superuser=None, verbose=True):
        super().__init__(session, name, password, superuser, verbose)

    def create(self) -> User:
        user_options_str = '{password}{superuser}'.format(password=' PASSWORD \'{}\''
                                                          .format(self.password) if self.password else '',
                                                          superuser='' if self.superuser is None else ' SUPERUSER'
                                                          if self.superuser else ' NOSUPERUSER')
        if user_options_str:
            user_options_str = ' WITH {}'.format(user_options_str)

        query = f'CREATE USER {self.name}{user_options_str}'
        if self.verbose:
            LOGGER.debug('Create user query: %s', query)

        self.session.execute(query)
        LOGGER.debug('User %s has been created', self.name)
        return self
